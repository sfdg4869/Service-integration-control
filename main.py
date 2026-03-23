from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from routers import postgres, oracle, rts, discovery
import os

app = FastAPI(title="Server Automation App")

# Create required directories if not exist
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# Mount Static and Templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Include Routers
app.include_router(discovery.router, prefix="/api/discovery", tags=["discovery"])
app.include_router(postgres.router, prefix="/api/postgres", tags=["postgres"])
app.include_router(oracle.router, prefix="/api/oracle", tags=["oracle"])
app.include_router(rts.router, prefix="/api/rts", tags=["rts"])

@app.get("/", response_class=HTMLResponse)
async def serve_frontend(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
