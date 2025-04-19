
from fastapi import FastAPI
import os, httpx, asyncio, time

app = FastAPI()
API_KEY = os.getenv("GEXBOT_API_KEY","demo")
URL = "https://api.gexbot.com/v1/levels?symbol=NQ"
_cache = {"ts":0,"data":{}}

async def fetch():
    headers={"Authorization":f"Bearer {API_KEY}"}
    async with httpx.AsyncClient() as c:
        r=await c.get(URL, headers=headers, timeout=5)
        return r.json()

async def get_data():
    if time.time()-_cache["ts"]<5:
        return _cache["data"]
    data=await fetch()
    _cache["ts"]=time.time()
    _cache["data"]=data
    return data

@app.get("/gexlive")
async def gexlive():
    return await get_data()
