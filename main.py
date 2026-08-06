import os
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import yfinance as yf
from openai import OpenAI

app = FastAPI(title="Pro Investment Terminal V7 - Bilingual", version="7.1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

YOUR_OPENROUTER_API_KEY = "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"
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=YOUR_OPENROUTER_API_KEY)

USERS_DB = {}

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

# 💡 就是这里！上一版不小心漏掉的返回模型，现在稳稳地加回来了！
class AnalyzeResponse(BaseModel):
    ticker: str
    ai_analysis: str

# 💡 你指定的专属免费模型排在绝对第一位！
FREE_MODELS_POOL = [
    "poolside/laguna-s-2.1:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "google/gemini-2.0-flash-exp:free"
]

def fetch_stock_internal(ticker: str) -> dict:
    symbol_str = ticker.upper()
    stock = yf.Ticker(symbol_str)
    info = stock.info
    
    current_price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("navPrice")
    if current_price is None: 
        raise ValueError(f"Target '{symbol_str}' not found.")
        
    prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
    change_pct = round(((current_price - prev_close) / prev_close) * 100, 2) if current_price and prev_close else 0.0
    
    return {
        "symbol": symbol_str,
        "name": info.get("longName") or info.get("shortName") or symbol_str,
        "current_price": current_price,
        "previous_close": prev_close,
        "change_percent": change_pct,
        "currency": info.get("currency", "USD"),
        "pe_ratio": info.get("trailingPE", "N/A")
    }

@app.post("/api/register")
def register_user(req: AuthRequest):
    if req.username in USERS_DB:
        raise HTTPException(status_code=400, detail="Username already exists!")
    USERS_DB[req.username] = {"password": req.password, "portfolio": []}
    return {"message": "Success"}

@app.post("/api/login")
def login_user(req: AuthRequest):
    user = USERS_DB.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(status_code=401, detail="Invalid credentials!")
    return {"message": "Success", "token": req.username}

@app.get("/api/stock/{ticker}")
def get_stock_data(ticker: str):
    try: return fetch_stock_internal(ticker)
    except Exception as e: raise HTTPException(status_code=404, detail=str(e))

@app.get("/api/portfolio")
def get_portfolio(x_user_id: str = Header(...)):
    if x_user_id not in USERS_DB:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    user_portfolio = USERS_DB[x_user_id]["portfolio"]
    result = []
    total_val, total_cost, grand_today_pnl = 0.0, 0.0, 0.0
    
    for item in user_portfolio:
        try:
            s_data = fetch_stock_internal(item["symbol"])
            cur_price = s_data["current_price"]
            prev_close = s_data["previous_close"]
            tot_val = cur_price * item["shares"]
            tot_cost = item["cost_price"] * item["shares"]
            pl_val = round(tot_val - tot_cost, 2)
            pl_pct = round((pl_val / tot_cost) * 100, 2) if tot_cost > 0 else 0.0
            today_pnl_val = round(item["shares"] * (cur_price - prev_close), 2) if prev_close else 0.0
            grand_today_pnl += today_pnl_val

            total_val += tot_val
            total_cost += tot_cost
            result.append({**item, "current_price": cur_price, "total_value": round(tot_val, 2), "profit_loss_val": pl_val, "profit_loss_pct": pl_pct, "today_pnl": today_pnl_val})
        except:
            result.append({**item, "current_price": 0, "total_value": 0, "profit_loss_val": 0, "profit_loss_pct": 0, "today_pnl": 0})

    grand_pl_val = round(total_val - total_cost, 2)
    grand_pl_pct = round((grand_pl_val / total_cost) * 100, 2) if total_cost > 0 else 0.0
    
    return {
        "items": result,
        "total_value": round(total_val, 2), "total_cost": round(total_cost, 2),
        "profit_loss_val": grand_pl_val, "profit_loss_pct": grand_pl_pct, "today_pnl": round(grand_today_pnl, 2)
    }

@app.post("/api/portfolio")
def save_portfolio(item: PortfolioItem, x_user_id: str = Header(...)):
    if x_user_id not in USERS_DB: raise HTTPException(status_code=401, detail="Unauthorized")
    user_portfolio = USERS_DB[x_user_id]["portfolio"]
    for existing in user_portfolio:
        if existing["symbol"].upper() == item.symbol.upper():
            existing["cost_price"] = item.cost_price
            existing["shares"] = item.shares
            return {"message": "Updated"}
    user_portfolio.append({"symbol": item.symbol.upper(), "cost_price": item.cost_price, "shares": item.shares})
    return {"message": "Added"}

@app.delete("/api/portfolio/{ticker}")
def delete_portfolio(ticker: str, x_user_id: str = Header(...)):
    if x_user_id not in USERS_DB: raise HTTPException(status_code=401, detail="Unauthorized")
    USERS_DB[x_user_id]["portfolio"] = [i for i in USERS_DB[x_user_id]["portfolio"] if i["symbol"].upper() != ticker.upper()]
    return {"message": "Removed"}

@app.get("/api/portfolio-alert")
def portfolio_alert(x_user_id: str = Header(...), x_language: str = Header("zh")):
    if x_user_id not in USERS_DB: raise HTTPException(status_code=401, detail="Unauthorized")
    user_portfolio = USERS_DB[x_user_id]["portfolio"]
    if not user_portfolio: return {"alert_report": "Empty portfolio / 账户为空"}
    
    portfolio_snapshot = []
    for item in user_portfolio:
        try:
            d = fetch_stock_internal(item["symbol"])
            pl_pct = round(((d['current_price'] - item['cost_price']) / item['cost_price']) * 100, 2)
            portfolio_snapshot.append(f"[{d['symbol']}] Cost: ${item['cost_price']} | Current: ${d['current_price']} | P&L: {pl_pct}%")
        except: pass

    if x_language == "en":
        sys_prompt = "You are a top Wall Street portfolio risk manager. Based on the user's portfolio data, evaluate the health of each stock. Give clear advice: [Add Position], [Hold], or [Take Profit/Cut Loss]. Output in highly professional English."
    else:
        sys_prompt = "你是严厉的华尔街长线资产风控主管。根据用户的持仓数据对每只股票给出明确的操作建议（加仓/持有/止盈割肉），中文输出。"

    user_prompt = f"Data:\n{portfolio_snapshot}"
    report, last_error = "", ""
    
    for model in FREE_MODELS_POOL:
        try:
            res = client.chat.completions.create(model=model, messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}], max_tokens=1500, temperature=0.7, timeout=30.0)
            report = f"*(🤖 Engine: {model})*\n\n" + res.choices[0].message.content
            break
        except Exception as e: last_error = str(e); continue 

    return {"alert_report": report or f"⚠️ AI Error: {last_error}"}

@app.post("/api/ai-analyze", response_model=AnalyzeResponse)
def analyze_stock(request: AnalyzeRequest):
    try:
        stock_data = fetch_stock_internal(request.ticker)
        
        if request.language == "en":
            if request.perspective == "financial": sys_prompt = "You are a strict CPA. Deep dive into PE ratio and financial valuation. Output in professional English."
            elif request.perspective == "technical": sys_prompt = "You are a quant trader. Evaluate technical momentum. Output in professional English."
            elif request.perspective == "stress": sys_prompt = "You are a pessimistic short seller. Stress test the stock and expose risks. Output in professional English."
            else: sys_prompt = "You are a top long-term investment analyst. Look for moats and 5-10 year holds. Output in professional English."
        else:
            if request.perspective == "financial": sys_prompt = "你是严谨的CPA，深挖市盈率与估值，中文输出。"
            elif request.perspective == "technical": sys_prompt = "你是量化交易员，评估技术面与动能，中文输出。"
            elif request.perspective == "stress": sys_prompt = "你是悲观的做空机构，极限挑刺排雷，中文输出。"
            else: sys_prompt = "你是顶尖长线投资分析师。寻找护城河，中文输出。"
            
        analysis_result, last_error = "", ""
        for model in FREE_MODELS_POOL:
            try:
                res = client.chat.completions.create(model=model, messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": f"Data:\n{stock_data}"}], max_tokens=1500, temperature=0.7, timeout=30.0)
                analysis_result = f"*(🔍 Engine: {model})*\n\n" + res.choices[0].message.content
                break 
            except Exception as e: last_error = str(e); continue
        
        return AnalyzeResponse(ticker=request.ticker.upper(), ai_analysis=analysis_result or f"⚠️ AI Error: {last_error}")
    except Exception as e:
         return AnalyzeResponse(ticker=request.ticker.upper(), ai_analysis=f"Error: {str(e)}")