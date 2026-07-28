import asyncio
import sys

if sys.platform == "win32":
    # uvicorn.run()이 이벤트 루프를 만들기 전에 정책을 바꿔야 한다 — app.main 안에서
    # 바꾸면 이미 늦다(uvicorn이 단일 프로세스 모드에서는 앱을 import하기 전에
    # asyncio.run()으로 루프를 먼저 만들어버려서, 그때는 이미 기본 Proactor 정책으로
    # 루프가 생성된 뒤라 되돌릴 수 없다).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
