from fastapi import FastAPI, Request
import json

app = FastAPI(title="The Acting Engine - Phase 4")

@app.post("/api/v1/fusion")
async def handle_fusion(request: Request):
    data = await request.json()
    print("\n" + "="*50)
    print("🔥 [The Acting Engine] Received IdentityDNA Request 🔥")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("="*50 + "\n")
    return {"status": "success", "message": "IdentityDNA 融合参数已接收并处理完毕。"}

@app.post("/api/v1/scene")
async def handle_scene(request: Request):
    data = await request.json()
    print("\n" + "="*50)
    print("🏞️ [The Acting Engine] Received SceneDNA Request 🏞️")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("="*50 + "\n")
    return {"status": "success", "message": "SceneDNA 场景参数已接收并处理完毕。"}

@app.post("/api/v1/prop")
async def handle_prop(request: Request):
    data = await request.json()
    print("\n" + "="*50)
    print("🛡️ [The Acting Engine] Received PropDNA Request 🛡️")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("="*50 + "\n")
    return {"status": "success", "message": "PropDNA 道具参数已接收并处理完毕。"}

@app.post("/api/v1/performance")
async def handle_performance(request: Request):
    data = await request.json()
    print("\n" + "="*50)
    print("🎭 [The Acting Engine] Received PerformanceDNA Request 🎭")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("="*50 + "\n")
    return {"status": "success", "message": "PerformanceDNA 表演参数已接收并处理完毕。"}

@app.post("/api/v1/vocal")
async def handle_vocal(request: Request):
    data = await request.json()
    print("\n" + "="*50)
    print("🗣️ [The Acting Engine] Received VocalDNA Request 🗣️")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("="*50 + "\n")
    return {"status": "success", "message": "VocalDNA 声音参数已接收并处理完毕。"}

@app.post("/api/v1/dismantle")
async def handle_dismantle(request: Request):
    data = await request.json()
    print("\n" + "="*50)
    print("🧠 [The Acting Engine] Received ScriptBrain Dismantle Fallback Request 🧠")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("="*50 + "\n")
    return {"status": "success", "message": "ScriptBrain 备用拆解请求已处理完毕。"}
