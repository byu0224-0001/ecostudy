from fastapi import FastAPI, Request
from fastapi.responses import Response

from radar.httpapp import ROOT, dispatch

app = FastAPI()


@app.api_route("/", methods=["GET", "POST", "HEAD"])
@app.api_route("/{full_path:path}", methods=["GET", "POST", "HEAD"])
async def entry(request: Request, full_path: str = "") -> Response:
    status, content_type, payload = dispatch(
        request.method,
        request.url.path,
        await request.body(),
        ROOT,
    )
    return Response(content=payload, status_code=status, headers={"Content-Type": content_type})
