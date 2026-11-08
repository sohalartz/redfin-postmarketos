import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

REPO = "sohalartz/redfin-postmarketos"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


def get_github_token():
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input=b"protocol=https\nhost=github.com\n\n",
        capture_output=True,
        check=True,
    )
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return None


def api_get(path, token, raw=False):
    url = path if path.startswith("http") else f"https://api.github.com/repos/{REPO}{path}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "redfin-ci-monitor")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
        return data if raw else json.loads(data.decode("utf-8"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_with_auth_redirect(url, token):
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "redfin-ci-monitor")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            redirect_url = e.headers["Location"]
            with urllib.request.urlopen(redirect_url, timeout=180) as r2:
                return r2.read()
        raise


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    token = get_github_token()
    runs = api_get("/actions/runs?per_page=5", token)
    wruns = runs.get("workflow_runs", [])
    if not wruns:
        print("No workflow runs found yet.")
        return

    r = wruns[0]
    run_id = r["id"]
    print(
        f"Run #{r['run_number']} (id={run_id}): status={r['status']} conclusion={r['conclusion']} url={r['html_url']}"
    )
    jobs = api_get(f"/actions/runs/{run_id}/jobs", token)
    for j in jobs.get("jobs", []):
        print(
            f"  Job '{j['name']}' (id={j['id']}): status={j['status']} conclusion={j['conclusion']}"
        )
        for s in j.get("steps", []):
            print(
                f"    Step {s['number']} [{s['status']}/{s['conclusion']}]: {s['name']}"
            )
        if cmd in ("logs", "errors"):
            log_bytes = download_with_auth_redirect(
                f"https://api.github.com/repos/{REPO}/actions/jobs/{j['id']}/logs",
                token,
            )
            os.makedirs(OUT_DIR, exist_ok=True)
            log_path = os.path.join(OUT_DIR, "job.log")
            with open(log_path, "wb") as f:
                f.write(log_bytes)
            log_text = log_bytes.decode("utf-8", errors="replace")
            lines = log_text.splitlines()
            if cmd == "logs":
                tail_n = int(sys.argv[2]) if len(sys.argv) > 2 else 120
                print(f"\n--- Last {tail_n} lines of job log ---")
                for ln in lines[-tail_n:]:
                    print(ln)
            elif cmd == "errors":
                print(f"\n--- Error lines in job log (total {len(lines)} lines) ---")
                for idx, ln in enumerate(lines):
                    low = ln.lower()
                    if "error:" in low or "error 1" in low or "error 2" in low or "fatal:" in low or "undefined reference" in low:
                        start = max(0, idx - 3)
                        end = min(len(lines), idx + 5)
                        for k in range(start, end):
                            print(f"{k+1}: {lines[k]}")
                        print("---")

    if cmd == "download":
        arts = api_get(f"/actions/runs/{run_id}/artifacts", token)
        for a in arts.get("artifacts", []):
            print(f"Downloading artifact '{a['name']}' ({a['size_in_bytes']} bytes)...")
            zip_bytes = download_with_auth_redirect(a["archive_download_url"], token)
            os.makedirs(OUT_DIR, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                zf.extractall(OUT_DIR)
            print(f"Extracted to {OUT_DIR}: {os.listdir(OUT_DIR)}")


if __name__ == "__main__":
    main()
