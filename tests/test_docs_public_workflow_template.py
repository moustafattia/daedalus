from pathlib import Path

import jsonschema
import pytest
import yaml

from workflows.contract import WORKFLOW_POLICY_KEY, load_workflow_contract_file


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_SPECIFIC_TOKEN = "yoyo" + "pod"
WORKFLOW_EXAMPLES = [
    (
        "change-delivery",
        REPO_ROOT / "docs" / "examples" / "change-delivery.workflow.md",
        REPO_ROOT / "daedalus" / "workflows" / "change_delivery" / "workflow.template.md",
        REPO_ROOT / "daedalus" / "workflows" / "change_delivery" / "schema.yaml",
    ),
    (
        "issue-runner",
        REPO_ROOT / "docs" / "examples" / "issue-runner.workflow.md",
        REPO_ROOT / "daedalus" / "workflows" / "issue_runner" / "workflow.template.md",
        REPO_ROOT / "daedalus" / "workflows" / "issue_runner" / "schema.yaml",
    ),
]


@pytest.mark.parametrize(("workflow_name", "template_path", "_payload_path", "schema_path"), WORKFLOW_EXAMPLES)
def test_public_workflow_template_validates_against_schema(workflow_name, template_path, _payload_path, schema_path):
    template = load_workflow_contract_file(template_path).config
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(template, schema)


@pytest.mark.parametrize(
    ("workflow_name", "template_path", "placeholder"),
    [
        ("change-delivery", REPO_ROOT / "docs" / "examples" / "change-delivery.workflow.md", "your-org-your-repo-change-delivery"),
        ("issue-runner", REPO_ROOT / "docs" / "examples" / "issue-runner.workflow.md", "your-org-your-repo-issue-runner"),
    ],
)
def test_public_workflow_template_uses_generic_placeholders(workflow_name, template_path, placeholder):
    text = template_path.read_text(encoding="utf-8").lower()
    assert PROJECT_SPECIFIC_TOKEN not in text
    assert placeholder in text
    assert "# workflow policy" in text


@pytest.mark.parametrize(("workflow_name", "template_path"), [(name, template, ) for name, template, _payload, _schema in WORKFLOW_EXAMPLES])
def test_public_workflow_template_uses_markdown_body_for_shared_policy(workflow_name, template_path):
    contract = load_workflow_contract_file(template_path)

    assert contract.config[WORKFLOW_POLICY_KEY]
    assert workflow_name in contract.config[WORKFLOW_POLICY_KEY].lower()


@pytest.mark.parametrize(("workflow_name", "template_path", "payload_template_path", "_schema_path"), WORKFLOW_EXAMPLES)
def test_payload_workflow_template_matches_docs_copy(workflow_name, template_path, payload_template_path, _schema_path):
    assert payload_template_path.read_text(encoding="utf-8") == template_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("workflow_name", "workflow_doc", "template_path", "payload_template_path", "_schema_path"),
    [
        (
            name,
            REPO_ROOT / "docs" / "workflows" / f"{name}.md",
            template,
            payload,
            schema,
        )
        for name, template, payload, schema in WORKFLOW_EXAMPLES
    ],
)
def test_workflow_docs_link_their_public_and_packaged_templates(
    workflow_name,
    workflow_doc,
    template_path,
    payload_template_path,
    _schema_path,
):
    text = workflow_doc.read_text(encoding="utf-8")

    assert f"docs/examples/{workflow_name}.workflow.md" in text
    assert payload_template_path.relative_to(REPO_ROOT).as_posix() in text
    assert template_path.exists()
    assert payload_template_path.exists()
