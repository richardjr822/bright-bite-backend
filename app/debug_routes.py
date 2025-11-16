from app.main import app

if __name__ == "__main__":
    for r in app.routes:
        try:
            methods = ",".join(sorted(r.methods)) if hasattr(r, "methods") else ""
            print(f"{r.path} {methods}")
        except Exception:
            # Fallback to raw representation if route inspection fails
            print(r)
