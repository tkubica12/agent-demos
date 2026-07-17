from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

import httpx
import matplotlib.pyplot as plt
import seaborn as sns
from azure.identity import DefaultAzureCredential
from PIL import Image as PillowImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def account_name_from_project_endpoint(project_endpoint: str) -> str:
    host = project_endpoint.split("://", maxsplit=1)[-1].split("/", maxsplit=1)[0]
    suffix = ".services.ai.azure.com"
    if not host.endswith(suffix):
        raise ValueError("The project endpoint does not use the expected Foundry host.")
    return host[: -len(suffix)]


def generate_ai_image(endpoint: str, output_path: Path) -> dict:
    token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token
    with httpx.Client(timeout=300) as client:
        response = client.post(
            f"{endpoint}/images/generations",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-image-2",
                "prompt": (
                    "Create a clean, original enterprise infographic for a fictional "
                    "support operations center. Show a central support agent connected "
                    "to governed tools, memory, traces, and a human approval checkpoint. "
                    "Use navy, cyan, teal, and warm amber, flat vector style, white "
                    "background, no logos, no trademarks, no small text."
                ),
                "size": "1536x1024",
                "quality": "medium",
                "output_format": "png",
            },
        )
        response.raise_for_status()
        payload = response.json()
    image_data = payload.get("data", [{}])[0].get("b64_json")
    if not isinstance(image_data, str) or not image_data:
        raise RuntimeError(f"Image generation returned no base64 image: {payload}")
    output_path.write_bytes(base64.b64decode(image_data))
    return {
        "model": "gpt-image-2",
        "size": "1536x1024",
        "revisedPrompt": payload.get("data", [{}])[0].get("revised_prompt"),
    }


def create_backlog_chart(output_path: Path) -> None:
    sns.set_theme(style="whitegrid", palette="crest")
    weeks = ["W1", "W2", "W3", "W4", "W5", "W6"]
    values = [128, 119, 111, 95, 76, 61]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    sns.lineplot(x=weeks, y=values, marker="o", linewidth=3, ax=ax)
    ax.fill_between(weeks, values, alpha=0.15)
    ax.set_title("Unresolved support backlog", fontsize=16, weight="bold")
    ax.set_xlabel("Operating week")
    ax.set_ylabel("Open cases")
    ax.annotate(
        "52% reduction",
        xy=("W6", 61),
        xytext=("W4", 120),
        arrowprops={"arrowstyle": "->", "color": "#d97706"},
        color="#92400e",
        weight="bold",
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def create_resolution_chart(output_path: Path) -> None:
    sns.set_theme(style="ticks", palette="mako")
    tiers = ["Standard", "Priority", "Critical"]
    hours = [18.4, 7.2, 2.1]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    chart = sns.barplot(x=tiers, y=hours, hue=tiers, legend=False, ax=ax)
    ax.set_title("Median time to resolution by priority", fontsize=16, weight="bold")
    ax.set_xlabel("Case priority")
    ax.set_ylabel("Hours")
    for bar, value in zip(chart.patches, hours, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.4,
            f"{value:.1f} h",
            ha="center",
            weight="bold",
        )
    sns.despine()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def table(data: list[list[str]], widths: list[float]) -> Table:
    result = Table(data, colWidths=widths, repeatRows=1)
    result.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f3b5d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#eef7fa")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#8bb8c8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return result


def build_operations_report(
    output_path: Path,
    architecture_image: Path,
    backlog_chart: Path,
    resolution_chart: Path,
) -> None:
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "Subtitle",
            parent=styles["Heading2"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#0f766e"),
            spaceAfter=18,
        )
    )
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="Northwind Support Operations Review",
        author="Foundry Showcase",
    )
    story = [
        Paragraph("Northwind Support Operations Review", styles["Title"]),
        Paragraph("Fictional Q2 operating report for the Foundry IQ demo", styles["Subtitle"]),
        Image(str(architecture_image), width=16 * cm, height=10.67 * cm),
        Spacer(1, 0.5 * cm),
        Paragraph(
            "Executive summary",
            styles["Heading1"],
        ),
        Paragraph(
            "Northwind Support reduced unresolved cases from 128 to 61 over six "
            "weeks while preserving explicit approval for high-impact case changes. "
            "The largest improvement came from separating read-only investigation, "
            "policy assessment, write proposals, and confirmed updates.",
            styles["BodyText"],
        ),
        PageBreak(),
        Paragraph("Operating outcomes", styles["Heading1"]),
        Image(str(backlog_chart), width=16 * cm, height=8.53 * cm),
        Spacer(1, 0.3 * cm),
        Image(str(resolution_chart), width=16 * cm, height=8.53 * cm),
        PageBreak(),
        Paragraph("Control evidence", styles["Heading1"]),
        table(
            [
                ["Control", "Evidence", "Owner"],
                [
                    "Read/write separation",
                    "Search and proposal tools cannot commit changes.",
                    "Support platform",
                ],
                [
                    "Human confirmation",
                    "Medium and high-risk changes pause at a durable checkpoint.",
                    "Case reviewer",
                ],
                [
                    "Bounded delegation",
                    "One read-only policy helper returns a structured assessment.",
                    "AI governance",
                ],
                [
                    "Memory privacy",
                    "Stable preferences are scoped per user; credentials are excluded.",
                    "Privacy lead",
                ],
            ],
            [4.0 * cm, 8.3 * cm, 3.5 * cm],
        ),
        Spacer(1, 0.5 * cm),
        Paragraph("Key metrics", styles["Heading2"]),
        table(
            [
                ["Metric", "Q1", "Q2", "Change"],
                ["Open cases", "128", "61", "-52%"],
                ["Median resolution", "14.7 h", "9.2 h", "-37%"],
                ["Confirmed write rate", "72%", "96%", "+24 pp"],
                ["Grounded answer rate", "84%", "94%", "+10 pp"],
            ],
            [5.0 * cm, 3.0 * cm, 3.0 * cm, 3.8 * cm],
        ),
    ]
    document.build(story)


def build_playbook(output_path: Path, architecture_image: Path) -> None:
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="Northwind Support Governance Playbook",
        author="Foundry Showcase",
    )
    story = [
        Paragraph("Northwind Support Governance Playbook", styles["Title"]),
        Paragraph(
            "This fictional playbook defines how the support agent investigates and "
            "updates cases. It is the authoritative source for the Foundry IQ demo.",
            styles["BodyText"],
        ),
        Spacer(1, 0.5 * cm),
        Image(str(architecture_image), width=16 * cm, height=10.67 * cm),
        PageBreak(),
        Paragraph("Case update sequence", styles["Heading1"]),
        table(
            [
                ["Step", "Required behavior", "Failure response"],
                ["1. Investigate", "Read the current case and related evidence.", "Do not guess missing state."],
                ["2. Assess", "Ask the policy helper for a read-only structured decision.", "Deny contradictory changes."],
                ["3. Propose", "Create a noncommitted update proposal.", "Return the proposal without applying it."],
                ["4. Confirm", "Pause medium/high-risk work for a human confirmation ID.", "Keep the checkpoint pending."],
                ["5. Apply", "Apply only the explicitly confirmed proposal.", "Reject missing or reused confirmation."],
            ],
            [2.5 * cm, 7.4 * cm, 5.9 * cm],
        ),
        Spacer(1, 0.6 * cm),
        Paragraph("Escalation matrix", styles["Heading1"]),
        table(
            [
                ["Condition", "Risk", "Required action"],
                ["Resolution note only", "Low", "May apply automatically through the governed proposal path."],
                ["Owner or ordinary priority change", "Medium", "Require reviewer confirmation."],
                ["Critical priority, escalation, or resolution", "High", "Require elevated confirmation and policy review."],
                ["Contradicts current evidence", "Deny", "Reject without creating a committed write."],
            ],
            [5.0 * cm, 2.5 * cm, 8.3 * cm],
        ),
        Spacer(1, 0.6 * cm),
        Paragraph("Privacy and memory", styles["Heading1"]),
        Paragraph(
            "Long-term memory may retain stable presentation preferences, project "
            "context, conversation summaries, and safe procedures. It must not retain "
            "credentials, authentication tokens, financial account data, precise "
            "location, or critical personal identifiers. A user can request deletion "
            "or correction of a stored memory.",
            styles["BodyText"],
        ),
    ]
    document.build(story)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "generated",
    )
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    account_name = account_name_from_project_endpoint(args.project_endpoint)
    openai_endpoint = f"https://{account_name}.openai.azure.com/openai/v1"
    architecture_image = args.output_dir / "support-architecture.png"
    backlog_chart = args.output_dir / "backlog-trend.png"
    resolution_chart = args.output_dir / "resolution-time.png"
    image_metadata = generate_ai_image(openai_endpoint, architecture_image)
    create_backlog_chart(backlog_chart)
    create_resolution_chart(resolution_chart)

    operations_report = args.output_dir / "northwind-support-operations.pdf"
    governance_playbook = args.output_dir / "northwind-support-governance.pdf"
    build_operations_report(
        operations_report,
        architecture_image,
        backlog_chart,
        resolution_chart,
    )
    build_playbook(governance_playbook, architecture_image)
    for path in (architecture_image, backlog_chart, resolution_chart):
        with PillowImage.open(path) as image:
            image.verify()

    manifest = {
        "imageGeneration": image_metadata,
        "documents": [
            {"path": str(operations_report), "bytes": operations_report.stat().st_size},
            {"path": str(governance_playbook), "bytes": governance_playbook.stat().st_size},
        ],
        "images": [
            str(architecture_image),
            str(backlog_chart),
            str(resolution_chart),
        ],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
