import os
import time
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import yfinance as yf
import requests
from openai import OpenAI

app = FastAPI(title="Pro Investment Terminal V9 - Anti-Block", version="9.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

YOUR_OPENROUTER_API_KEY = "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"
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=YOUR_OPENROUTER_API_KEY)

# 💡 破解限流法宝 1：伪装成人类真实浏览器
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5"
})

# 💡 破解限流法宝 2：建立数据缓存（同一只股票 60 秒内不再重复打扰 Yahoo）
PRICE_CACHE = {}
CACHE_EXPIRY = 60 

class AnalyzeRequest(BaseModel):
    ticker: str
    perspective: str = "default"
    language: str = "zh"

class AnalyzeResponse(BaseModel):
    ticker: str
    ai_analysis: str

class AlertRequest(BaseModel):
    portfolio_data: str
    language: str = "zh"

FREE_MODELS_POOL = [
    "poolside/laguna-s-2.1:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "google/gemini-2.0-flash-exp:free"
]

def fetch_stock_internal(ticker: str) -> dict:
    symbol_str = ticker.upper()
    current_time = time.time()
    
    # 如果缓存里有，且还没过期，直接极速返回！
    if symbol_str in PRICE_CACHE and (current_time - PRICE_CACHE[symbol_str]['timestamp']) < CACHE_EXPIRY:
        return PRICE_CACHE[symbol_str]['data']

    try:
        # 使用带有浏览器伪装的 session
        stock = yf.Ticker(symbol_str, session=session)
        info = stock.info
        
        current_price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("navPrice")
        if current_price is None: 
            # 备用极速抓取通道
            current_price = stock.fast_info.get("last_price")
            
        if current_price is None:
            raise ValueError(f"Target '{symbol_str}' not found.")
            
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        change_pct = round(((current_price - prev_close) / prev_close) * 100, 2) if current_price and prev_close else 0.0
        
        result = {
            "symbol": symbol_str,
            "name": info.get("longName") or info.get("shortName") or symbol_str,
            "current_price": current_price,
            "previous_close": prev_close,
            "change_percent": change_pct,
            "currency": info.get("currency", "USD"),
            "pe_ratio": info.get("trailingPE", "N/A")
        }
        
        # 存入缓存
        PRICE_CACHE[symbol_str] = {'timestamp': current_time, 'data': result}
        return result
    except Exception as e:
        raise ValueError(f"Fetch failed: {str(e)}")

@app.get("/api/stock/{ticker}")
def get_stock_data(ticker: str):
    try: return fetch_stock_internal(ticker)
    except Exception as e: raise HTTPException(status_code=429, detail=str(e)) 

@app.post("/api/portfolio-alert")
def portfolio_alert(req: AlertRequest):
    sys_prompt = "你是严厉的华尔街长线资产风控主管。根据用户的持仓数据对每只股票给出明确的操作建议，中文输出。" if req.language == "zh" else "You are a top Wall Street portfolio risk manager. Evaluate the health of each stock. Output in professional English."
    
    report, last_error = "", ""
    for model in FREE_MODELS_POOL:
        try:
            res = client.chat.completions.create(model=model, messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": f"Data:\n{req.portfolio_data}"}], max_tokens=1500, temperature=0.7, timeout=25.0)
            report = f"*(🤖 Engine: {model})*\n\n" + res.choices[0].message.content
            break
        except Exception as e: last_error = str(e); continue 

    return {"alert_report": report or f"⚠️ AI Request Limit Reached. AI 线路拥堵，请稍后重试。"}

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
            
        analysis_result, last_error = "", ""
        for model in FREE_MODELS_POOL:
            try:
                res = client.chat.completions.create(model=model, messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": f"Data:\n{stock_data}"}], max_tokens=1500, temperature=0.7, timeout=25.0)
                analysis_result = f"*(🔍 Engine: {model})*\n\n" + res.choices[0].message.content
                break 
            except Exception as e: last_error = str(e); continue
        
        return AnalyzeResponse(ticker=request.ticker.upper(), ai_analysis=analysis_result or f"⚠️ AI Request Limit Reached. 免费 AI 通道排队中，请稍后再试。")
    except Exception as e:
         return AnalyzeResponse(ticker=request.ticker.upper(), ai_analysis=f"⚠️ 请求限制 (Too Many Requests): 已被 Yahoo 财经防火墙暂时拦截，请休息几分钟后再试。")