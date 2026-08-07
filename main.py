import os
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import yfinance as yf
from openai import OpenAI

app = FastAPI(title="Pro Investment Terminal V8 - Serverless", version="8.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

YOUR_OPENROUTER_API_KEY = "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"
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=YOUR_OPENROUTER_API_KEY)

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

@app.get("/api/stock/{ticker}")
def get_stock_data(ticker: str):
    try: return fetch_stock_internal(ticker)
    except Exception as e: raise HTTPException(status_code=404, detail=str(e))

@app.post("/api/portfolio-alert")
def portfolio_alert(req: AlertRequest):
    if req.language == "en":
        sys_prompt = "You are a top Wall Street portfolio risk manager. Based on the user's portfolio data, evaluate the health of each stock. Give clear advice: [Add Position], [Hold], or [Take Profit/Cut Loss]. Output in highly professional English."
    else:
        sys_prompt = "你是严厉的华尔街长线资产风控主管。根据用户的持仓数据对每只股票给出明确的操作建议（加仓/持有/止盈割肉），中文输出。"

    user_prompt = f"Data:\n{req.portfolio_data}"
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