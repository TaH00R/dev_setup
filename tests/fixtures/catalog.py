"""Synthetic catalog data; no live registry or machine-specific values."""


def example_document():
    return {
        "schema_version": 1,
        "package": {
            "id": "example.tool",
            "name": "Example Tool",
            "summary": "Synthetic tool for tests",
            "category": "utilities",
            "homepage": "https://example.org",
            "install_specs": [
                {
                    "support": {"os": "windows", "architectures": ["x86_64"]},
                    "backend": "winget",
                    "native_id": "Example.Tool",
                    "privilege": "native_prompt",
                    "source": {
                        "id": "example",
                        "name": "Example",
                        "kind": "curated",
                        "trust": "community_repository",
                        "url": "https://example.org",
                    },
                    "review": {
                        "url": "https://example.org/package",
                        "checked_on": "2026-11-11",
                    },
                }
            ],
            "detection": [{"executable": "example", "version_args": ["--version"]}],
        },
    }
