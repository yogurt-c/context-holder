"""`ctx` 명령의 진입점."""

import typer

from context_holder import __version__

app = typer.Typer(
    name="ctx",
    help="ctx: 빅테크 기술 결정 사례 위키를 수집하고 검색한다.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"ctx {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="버전을 출력하고 끝낸다.",
        callback=_print_version,
        is_eager=True,
    ),
) -> None:
    """ctx: 빅테크 기술 결정 사례 위키를 수집하고 검색한다."""
