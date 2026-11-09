"""묶은 엔진(ctx-engine)의 진입점. 개발 환경의 `ctx` 명령과 같다."""

from context_holder.cli import app

if __name__ == "__main__":
    app()
