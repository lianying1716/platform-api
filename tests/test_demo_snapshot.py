"""Demo snapshot and model recommendation share their production contracts."""

from pathlib import Path

from app.core.crypto import encrypt_credential
from app.models import AiModel, AiProvider, ModelAssignment, SystemSetting


ROOT = Path(__file__).resolve().parent.parent


class _PagedClient:
    def __init__(self, rows):
        self.rows = rows

    def get(self, path):
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(path).query)
        limit = int(query.get('limit', ['200'])[0])
        offset = int(query.get('offset', ['0'])[0])
        rows = self.rows

        class Response:
            status_code = 200
            headers = {'X-Total-Count': str(len(rows))}

            def json(self):
                return rows[offset:offset + limit]

        return Response()


def test_demo_snapshot_fetches_every_page_beyond_200_items():
    from scripts.export_demo_snapshot import _fetch_all_pages

    rows = [{'asset_id': i} for i in range(465)]
    fetched = _fetch_all_pages(_PagedClient(rows), '/api/assets', page_size=200)
    assert fetched == rows
    assert len(fetched) == 465


def test_demo_snapshot_fails_instead_of_publishing_a_partial_list():
    from scripts.export_demo_snapshot import _fetch_all_pages

    class _Broken:
        def get(self, path):
            class Response:
                status_code = 500
                headers = {}

                def json(self):
                    return []

            return Response()

    try:
        _fetch_all_pages(_Broken(), '/api/assets')
    except RuntimeError as exc:
        assert 'HTTP 500' in str(exc)
    else:
        raise AssertionError('partial demo snapshot must not be published')


def test_paginate_snapshot_has_no_fake_total_for_object_responses():
    """Only arrays carry a list total; a detail object is not a one-row page."""
    source = (ROOT / "web" / "src" / "demo" / "runtime.ts").read_text(encoding="utf-8")
    assert "if (!Array.isArray(value)) return { data: value, total: null }" in source
    api_source = (ROOT / "web" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "if (Array.isArray(paged.data) && paged.total !== null)" in api_source

def _provider(session, key='key'):
    provider = AiProvider(
        name='p', preset_key='custom', protocol='openai', base_url='https://api.example.com/v1',
        api_key_encrypted=encrypt_credential({'api_key': key}), enabled=True, config_revision=1,
    )
    session.add(provider)
    session.flush()
    return provider


def test_recommend_assignments_obeys_runtime_cover_image_requirement(client, session):
    provider = _provider(session)
    text_only = AiModel(
        provider_id=provider.id,
        model_key='text-only',
        capabilities={
            'text': {'value': True, 'source': 'probe', 'config_revision': 1},
            'json': {'value': True, 'source': 'probe', 'config_revision': 1},
            'image': {'value': False, 'source': 'probe', 'config_revision': 1},
        },
    )
    image_model = AiModel(
        provider_id=provider.id,
        model_key='image-text',
        capabilities={
            'text': {'value': True, 'source': 'probe', 'config_revision': 1},
            'json': {'value': True, 'source': 'probe', 'config_revision': 1},
            'image': {'value': True, 'source': 'probe', 'config_revision': 1},
        },
    )
    session.add_all([text_only, image_model])
    session.add_all([
        SystemSetting(key='process_classify_enabled', value='true'),
        SystemSetting(key='process_use_cover', value='true'),
    ])
    session.commit()

    assert client.post('/api/model-access/assignments/recommend').status_code == 200
    session.expire_all()
    assignment = session.query(ModelAssignment).filter_by(work_type='cover_classify').one()
    assert assignment.primary_model_id == image_model.id


def test_recommend_assignments_skips_unusable_provider_credentials(client, session):
    provider = _provider(session, key='')
    model = AiModel(
        provider_id=provider.id,
        model_key='legacy',
        capabilities={
            'text': {'value': True, 'source': 'legacy', 'config_revision': 1},
            'json': {'value': True, 'source': 'legacy', 'config_revision': 1},
            'video': {'value': True, 'source': 'legacy', 'config_revision': 1},
        },
    )
    session.add(model)
    session.commit()

    assert client.post('/api/model-access/assignments/recommend').status_code == 200
    session.expire_all()
    assert session.query(ModelAssignment).count() == 0
