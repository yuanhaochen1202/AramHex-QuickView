"""Render the production launcher without importing application code.

Each release supplies a complete versioned recovery ZIP. Read its version from
source as data, without executing the application or changing launcher output.
"""
import ast
from pathlib import Path
import re
from urllib.parse import urlsplit


RECOVERY_HOST = "aramhex-updates-1512332557.cos.ap-guangzhou.myqcloud.com"


def recovery_url_for_source(root: Path) -> str:
    """Read only the literal release version; never import or execute __init__.py."""
    path = Path(root) / "lolhex/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__version__"
                           for target in node.targets)]
    if (len(assignments) != 1 or len(assignments[0].targets) != 1
            or not isinstance(assignments[0].value, ast.Constant)
            or not isinstance(assignments[0].value.value, str)
            or not re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){2,3}", assignments[0].value.value)):
        raise ValueError("Source must declare exactly one literal three- or four-part release version")
    version = assignments[0].value.value
    return f"https://{RECOVERY_HOST}/downloads/AramHex-Portable-{version}-user.zip"


USER_RECOVERY_URL = recovery_url_for_source(Path(__file__).resolve().parents[1])


def render_launcher(root: Path, channel: str, recovery_url: str | None = None) -> str:
    if channel not in {"maintainer", "user"}:
        raise ValueError("Unknown production launcher channel")
    if channel == "maintainer":
        if recovery_url:
            raise ValueError("Maintainer launcher must not advertise a downloadable program")
        url = ""
    else:
        url = recovery_url_for_source(root) if recovery_url is None else recovery_url
        if url:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.netloc != RECOVERY_HOST
                    or parsed.query or parsed.fragment
                    or not re.fullmatch(r"/downloads/AramHex-Portable-[0-9]{1,5}(?:\.[0-9]{1,5}){2,3}-user\.zip", parsed.path)):
                raise ValueError("User recovery URL must name a complete user ZIP on the fixed HTTPS COS host")
    body = (Path(root) / "packaging/launcher.py").read_text(encoding="utf-8-sig")
    for name, value in (("BUILD_CHANNEL", channel), ("USER_RECOVERY_URL", url)):
        body, count = re.subn(r"(?m)^" + name + r" = [^\n]*$", lambda _: f"{name} = {value!r}", body)
        if count != 1:
            raise ValueError(f"Production launcher must have exactly one {name} build constant")
    return body
