import os
import time
import urllib.request
import json
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
from openai import OpenAI
from pymongo import MongoClient

app = FastAPI(title="Pro Investment Terminal V12 - Finnhub", version="12.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 💡 数据库连接逻辑 (保留不动)
MONGO_URI = os.getenv("MONGO_URI", "")
if MONGO_URI:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client["investment_terminal"]
    users_collection = db["users"]
else:
    users_collection = None 

# ============== 👇 唯一的改动在这里 👇 ==============
# 请把你刚刚在 Finnhub 复制的 API Key 粘贴到下面的引号里
FINNHUB_API_KEY = "d9r3gnpr01qnlhcl66igd9r3gnpr01qnlhcl66j0"
# ==================================================

YOUR_OPENROUTER_API_KEY = "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"
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=YOUR_OPENROUTER_API_KEY)

PRICE_CACHE = {}
CACHE_EXPIRY = 60 

class AuthRequest(BaseModel):
    username: str
    password: str

class PortfolioItem(BaseModel):
    symbol: str
    cost_price: float
    shares: float

class AnalyzeRequest(BaseModel):
    ticker: str
    perspective: str = "default"
    language: str = "zh"

class AlertRequest(BaseModel):
    portfolio_data: str
    language: str = "zh"

FREE_MODELS_POOL = [
    "poolside/laguna-s-2.1:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "google/gemini-2.0-flash-exp:free"
]

# ----------------- 用户系统 API (MongoDB - 保留不动) -----------------
@app.post("/api/register")
def register(req: AuthRequest):
    if users_collection is None: raise HTTPException(500, "Database not configured on server")
    if users_collection.find_one({"_id": req.username}):
        raise HTTPException(400, "User already exists")
    users_collection.insert_one({"_id": req.username, "password": req.password, "portfolio": []})
    return {"message": "Success"}

@app.post("/api/login")
def login(req: AuthRequest):
    if users_collection is None: raise HTTPException(500, "Database not configured on server")
    user = users_collection.find_one({"_id": req.username})
    if not user or user["password"] != req.password:
        raise HTTPException(401, "Invalid credentials")
    return {"token": req.username}

@app.get("/api/portfolio")
def get_portfolio(x_user_id: str = Header(...)):
    if users_collection is None: raise HTTPException(500, "Database not configured on server")
    user = users_collection.find_one({"_id": x_user_id})
    if not user: raise HTTPException(401, "Unauthorized")
    return {"portfolio": user.get("portfolio", [])}

@app.post("/api/portfolio")
def update_portfolio(item: PortfolioItem, x_user_id: str = Header(...)):
    if users_collection is None: raise HTTPException(500, "Database not configured on server")
    user = users_collection.find_one({"_id": x_user_id})
    if not user: raise HTTPException(401, "Unauthorized")
    
    portfolio = user.get("portfolio", [])
    found = False
    for p in portfolio:
        if p["symbol"] == item.symbol.upper():
            p["cost_price"] = item.cost_price
            p["shares"] = item.shares
            found = True
            break
    if not found:
        portfolio.append({"symbol": item.symbol.upper(), "cost_price": item.cost_price, "shares": item.shares})
        
    users_collection.update_one({"_id": x_user_id}, {"$set": {"portfolio": portfolio}})
    return {"message": "Success"}

@app.delete("/api/portfolio/{ticker}")
def delete_portfolio(ticker: str, x_user_id: str = Header(...)):
    if users_collection is None: raise HTTPException(500, "Database not configured on server")
    user = users_collection.find_one({"_id": x_user_id})
    if not user: raise HTTPException(401, "Unauthorized")
    
    portfolio = [p for p in user.get("portfolio", []) if p["symbol"] != ticker.upper()]
    users_collection.update_one({"_id": x_user_id}, {"$set": {"portfolio": portfolio}})
    return {"message": "Success"}

# ----------------- 数据与 AI API (彻底切换为 Finnhub) -----------------
def fetch_stock_internal(ticker: str) -> dict:
    symbol_str = ticker.upper()
    current_time = time.time()
    
    if symbol_str in PRICE_CACHE and (current_time - PRICE_CACHE[symbol_str]['timestamp']) < CACHE_EXPIRY:
        return PRICE_CACHE[symbol_str]['data']

    if not FINNHUB_API_KEY or FINNHUB_API_KEY == "请把你的密钥粘贴在这里":
        raise ValueError("System Error: Finnhub API Key is missing in backend.")

    try:
        # 获取最新价格
        url_quote = f"https://finnhub.io/api/v1/quote?symbol={symbol_str}&token={FINNHUB_API_KEY}"
        req_quote = urllib.request.Request(url_quote)
        with urllib.request.urlopen(req_quote, timeout=10) as response:
            data = json.loads(response.read().decode())
            
        current_price = data.get('c')
        prev_close = data.get('pc')
        
        if current_price is None or (current_price == 0 and prev_close == 0):
            raise ValueError("Target not found.")
            
        change_pct = round(((current_price - prev_close) / prev_close) * 100, 2) if current_price and prev_close else 0.0
        
        # 获取市盈率 PE 和公司全名
        pe_ratio = "N/A"
        stock_name = symbol_str
        try:
            url_profile = f"https://finnhub.io/api/v1/stock/profile2?symbol={symbol_str}&token={FINNHUB_API_KEY}"
            with urllib.request.urlopen(urllib.request.Request(url_profile), timeout=5) as resp:
                prof_data = json.loads(resp.read().decode())
                if prof_data and prof_data.get('name'):
                    stock_name = prof_data.get('name')
                    
            url_metric = f"https://finnhub.io/api/v1/stock/metric?symbol={symbol_str}&metric=all&token={FINNHUB_API_KEY}"
            with urllib.request.urlopen(urllib.request.Request(url_metric), timeout=5) as resp:
                metric_data = json.loads(resp.read().decode())
                pe = metric_data.get('metric', {}).get('peExclExtraTTM')
                if pe: pe_ratio = str(round(pe, 2))
        except Exception:
            pass 
        
        result = {
            "symbol": symbol_str,
            "name": stock_name, 
            "current_price": current_price,
            "previous_close": prev_close,
            "change_percent": change_pct,
            "currency": "USD",
            "pe_ratio": pe_ratio
        }
        
        PRICE_CACHE[symbol_str] = {'timestamp': current_time, 'data': result}
        return result
    except Exception as e:
        raise ValueError(f"Data Fetch Failed: {str(e)}")

@app.get("/api/stock/{ticker}")
def get_stock_data(ticker: str):
    try: return fetch_stock_internal(ticker)
    except Exception as e: raise HTTPException(status_code=400, detail=str(e)) 

@app.post("/api/portfolio-alert")
def portfolio_alert(req: AlertRequest):
    sys_prompt = "你是严厉的华尔街长线资产风控主管。根据用户的持仓数据对每只股票给出明确的操作建议，中文输出。" if req.language == "zh" else "You are a top Wall Street portfolio risk manager. Evaluate the health of each stock. Output in professional English."
    report = ""
    for model in FREE_MODELS_POOL:
        try:
            res = client.chat.completions.create(model=model, messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": f"Data:\n{req.portfolio_data}"}], max_tokens=1500, temperature=0.7, timeout=25.0)
            report = f"*(🤖 Engine: {model})*\n\n" + res.choices[0].message.content
            break
        except Exception: continue 
    return {"alert_report": report or f"⚠️ AI 服务暂时拥堵，请稍后重试。"}

@app.post("/api/ai-analyze", response_model=AnalyzeResponse)
def analyze_stock(request: AnalyzeRequest):
    try:
        stock_data = fetch_stock_internal(request.ticker)
        if request.language == "zh":
            if request.perspective == "financial": sys_prompt = "你是严谨的CPA，深挖市盈率与估值，中文输出。"
            elif request.perspective == "technical": sys_prompt = "你是量化交易员，评估技术面与动能，中文输出。"
            elif request.perspective == "stress": sys_prompt = "你是悲观的做空机构，极限挑刺排雷，中文输出。"
            else: sys_prompt = "你是顶尖长线投资分析师。寻找护城河，中文输出。"
        else:
            sys_prompt = "You are a top long-term investment analyst. Output in professional English."
            
        analysis_result = ""
        for model in FREE_MODELS_POOL:
            try:
                res = client.chat.completions.create(model=model, messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": f"Data:\n{stock_data}"}], max_tokens=1500, temperature=0.7, timeout=25.0)
                analysis_result = f"*(🔍 Engine: {model})*\n\n" + res.choices[0].message.content
                break 
            except Exception: continue
        return AnalyzeResponse(ticker=request.ticker.upper(), ai_analysis=analysis_result or f"⚠️ AI 服务暂时拥堵，请稍后重试。")
    except Exception as e:
         return AnalyzeResponse(ticker=request.ticker.upper(), ai_analysis=f"⚠️ 数据获取失败: {str(e)}")