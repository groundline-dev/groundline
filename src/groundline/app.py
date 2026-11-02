from fastapi import FastAPI

app = FastAPI(title="groundline")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
