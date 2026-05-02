from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "daedalus-ticket-progress"


def test_ticket_progress_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_ticket_progress_script_help_documents_verbose_watch():
    completed = subprocess.run(
        [str(SCRIPT), "--help"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert "watch" in completed.stdout
    assert "--verbose" in completed.stdout
    assert "DAEDALUS_PROGRESS_AGENT_MESSAGES" in completed.stdout


def test_ticket_progress_script_does_not_pin_operator_home():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "/home/raouf" not in source
