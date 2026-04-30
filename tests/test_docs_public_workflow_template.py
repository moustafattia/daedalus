from pathlib import Path

import jsonschema
import yaml

from workflows.contract import WORKFLOW_POLICY_KEY, load_workflow_contract_file


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "docs" / "examples" / "change-delivery.workflow.md"
PAYLOAD_TEMPLATE_PATH = (
    REPO_ROOT / "daedalus" / "workflows" / "change_delivery" / "workflow.template.md"
)
SCHEMA_PATH = REPO_ROOT / "daedalus" / "workflows" / "change_delivery" / "schema.yaml"


def test_public_workflow_template_validates_against_schema():
    template = load_workflow_contract_file(TEMPLATE_PATH).config
    schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(template, schema)


def test_public_workflow_template_uses_generic_placeholders():
    text = TEMPLATE_PATH.read_text(encoding="utf-8").lower()
    assert "yoyopod" not in text
    assert "your-org-your-repo-change-delivery" in text
    assert "# workflow policy" in text


def test_public_workflow_template_uses_markdown_body_for_shared_policy():
    contract = load_workflow_contract_file(TEMPLATE_PATH)

    assert contract.config[WORKFLOW_POLICY_KEY]
    assert "keep scope narrow" in contract.config[WORKFLOW_POLICY_KEY].lower()


def test_payload_workflow_template_matches_docs_copy():
    assert PAYLOAD_TEMPLATE_PATH.read_text(encoding="utf-8") == TEMPLATE_PATH.read_text(encoding="utf-8")
