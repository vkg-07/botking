from aiohttp import web

async def callback(request):
    print(str(request.url), flush=True)
    return web.Response(text="Authenticated! You can close this tab.")

app = web.Application()
app.router.add_get("/callback", callback)
web.run_app(app, port=8888)
