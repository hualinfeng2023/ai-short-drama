import io
import zipfile
from urllib.parse import quote

import pytest
from docx import Document
from httpx import AsyncClient
from pypdf import PdfWriter

from app.services.media import deterministic_png_bytes

pytestmark = pytest.mark.anyio

PROJECT_PAYLOAD = {
    "name": "素材上传安全测试",
    "idea": "一场暴雨把三个陌生人困在便利店，他们共享同一个秘密。",
    "genre": "urban_suspense",
    "style": "realistic_cinematic",
    "target_duration_sec": 60,
    "aspect_ratio": "9:16",
    "target_platform": "douyin",
    "reference_asset_ids": [],
    "assumptions": [],
}


async def _project(client: AsyncClient, key: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/projects",
        json=PROJECT_PAYLOAD,
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201
    return response.json()["data"]["project"]


async def _upload(
    client: AsyncClient,
    project_id: str,
    filename: str,
    content: bytes,
    content_type: str,
    *,
    rights: bool = True,
):  # noqa: ANN202
    return await client.post(
        f"/api/v1/projects/{project_id}/assets",
        content=content,
        headers={
            "Content-Type": content_type,
            "X-Filename": quote(filename, safe=""),
            "X-Rights-Confirmed": str(rights).lower(),
        },
    )


async def test_text_upload_deduplicates_and_links_to_immutable_brief(
    client: AsyncClient,
) -> None:
    project = await _project(client, "upload-project-text-v1")
    project_id = str(project["id"])
    content = "人物小传：林悦在雨夜决定重新选择自己。".encode()

    rights_blocked = await _upload(
        client, project_id, "人物小传.txt", content, "text/plain", rights=False
    )
    assert rights_blocked.status_code == 423
    assert rights_blocked.json()["error"]["code"] == "RIGHTS_REQUIRED"

    created = await _upload(client, project_id, "人物小传.txt", content, "text/plain")
    assert created.status_code == 201
    asset = created.json()["data"]
    assert asset["kind"] == "REFERENCE_TEXT"
    assert asset["metadata"]["parse_status"] == "READY"
    assert asset["metadata"]["parsed_text"].startswith("人物小传")
    assert asset["rights_status"] == "USER_CONFIRMED"
    assert asset["is_temporary"] is False

    replay = await _upload(client, project_id, "人物小传.txt", content, "text/plain")
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == asset["id"]
    listed = (await client.get(f"/api/v1/projects/{project_id}/assets")).json()["data"]
    assert [item["id"] for item in listed] == [asset["id"]]
    downloaded = await client.get(asset["content_url"])
    assert downloaded.content == content

    forged_reference = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={
            "expected_version": 1,
            "reference_asset_ids": ["00000000-0000-4000-8000-000000000099"],
        },
    )
    assert forged_reference.status_code == 422

    linked = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"expected_version": 1, "reference_asset_ids": [asset["id"]]},
    )
    assert linked.status_code == 200
    blocked_delete = await client.delete(f"/api/v1/assets/{asset['id']}")
    assert blocked_delete.status_code == 409


async def test_document_and_image_upload_parsing(client: AsyncClient) -> None:
    project = await _project(client, "upload-project-docs-v1")
    project_id = str(project["id"])

    pdf_buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=320, height=480)
    writer.write(pdf_buffer)
    pdf = await _upload(
        client,
        project_id,
        "参考.pdf",
        pdf_buffer.getvalue(),
        "application/pdf",
    )
    assert pdf.status_code == 201
    assert pdf.json()["data"]["metadata"]["parse_status"] == "UNSUPPORTED_OCR"

    document = Document()
    document.add_paragraph("第一幕：停电后的便利店")
    docx_buffer = io.BytesIO()
    document.save(docx_buffer)
    docx = await _upload(
        client,
        project_id,
        "大纲.docx",
        docx_buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert docx.status_code == 201
    assert "第一幕" in docx.json()["data"]["metadata"]["parsed_text"]

    png = await _upload(
        client,
        project_id,
        "参考图.png",
        deterministic_png_bytes(64, 96, "upload-test"),
        "image/png",
    )
    assert png.status_code == 201
    assert (png.json()["data"]["width"], png.json()["data"]["height"]) == (64, 96)


async def test_upload_rejects_oversize_magic_mismatch_and_unsafe_archive(
    client: AsyncClient,
) -> None:
    project = await _project(client, "upload-project-security-v1")
    project_id = str(project["id"])

    unsupported = await _upload(client, project_id, "archive.zip", b"PK\x03\x04", "application/zip")
    assert unsupported.status_code == 415

    traversal_name = await _upload(client, project_id, "../escape.txt", b"unsafe", "text/plain")
    assert traversal_name.status_code == 422

    fake_image = await _upload(client, project_id, "fake.png", b"not-png", "image/png")
    assert fake_image.status_code == 422

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("word/document.xml", "<document />")
        archive.writestr("../escape", "blocked")
    unsafe_docx = await _upload(
        client,
        project_id,
        "unsafe.docx",
        archive_buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert unsafe_docx.status_code == 422

    oversize = await _upload(
        client,
        project_id,
        "too-large.txt",
        b"a" * (10 * 1024 * 1024 + 1),
        "text/plain",
    )
    assert oversize.status_code == 413
