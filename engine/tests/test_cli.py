from typer.testing import CliRunner

from context_holder import __version__
from context_holder.cli import app

runner = CliRunner()


def test_help_lists_program_name():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "ctx" in result.output


def test_version_prints_package_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"ctx {__version__}"
