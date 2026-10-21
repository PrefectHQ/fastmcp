class ArticleNotFound(Exception):
    pass


def docs_status() -> dict[str, str | bool]:
    return {"service": "docs", "ready": True}


def ops_status() -> dict[str, str | bool]:
    return {"service": "ops", "ready": True}


def get_article(slug: str) -> dict[str, str]:
    if slug == "broken":
        raise RuntimeError(
            "Postgres connection failed at db.internal:5432; password=synthetic-test-password"
        )
    if slug != "install":
        raise ArticleNotFound(slug)
    return {
        "slug": "install",
        "title": "Install the client",
        "body": "Run pip install example-client.",
    }
