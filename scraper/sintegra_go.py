from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import Settings
from app.core.constants import DEFAULT_BROWSER_HEADERS

logger = logging.getLogger(__name__)


class ScrapeError(Exception):
    """Erro previsível ao interpretar o HTML ou submeter o formulário."""


_COLON_PAIR = re.compile(r"^([^:]+?):\s*(.+)$")


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _maybe_fix_mojibake(s: str) -> str:
    """UTF-8 lido como latin-1/cp1252 vira 'nÃ£o' em vez de 'não'."""
    if "Ã" not in s:
        return s
    for enc in ("latin-1", "cp1252"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return s


def _decode_response_body(resp: httpx.Response) -> str:
    raw = resp.content or b""
    if not raw:
        return ""
    ctype = (resp.headers.get("content-type") or "").lower()
    declared: str | None = None
    if "charset=" in ctype:
        piece = ctype.split("charset=")[-1].split(";")[0].strip()
        declared = piece.strip('"').strip("'")
    seen: set[str] = set()
    candidates: list[str] = []
    for enc in (
        declared,
        getattr(resp, "encoding", None),
        "utf-8",
        "iso-8859-1",
        "windows-1252",
    ):
        if not enc or not isinstance(enc, str):
            continue
        e = enc.strip()
        if not e or e.lower() in seen:
            continue
        seen.add(e.lower())
        candidates.append(e)
    for enc in candidates:
        try:
            s = raw.decode(enc)
        except (UnicodeDecodeError, LookupError, TypeError):
            continue
        s = _maybe_fix_mojibake(s)
        if enc.lower().startswith("utf") and "\ufffd" in s:
            continue
        return s
    return raw.decode("latin-1", errors="replace")


def _looks_like_nao_encontrado(text: str) -> bool:
    t = _strip_accents(_maybe_fix_mojibake(text)).lower()
    return "nao encontrado" in t


def _response_html(resp: httpx.Response) -> str:
    return _decode_response_body(resp)


def _pick_consulta_form(soup: BeautifulSoup):
    forms = soup.find_all("form")
    for f in forms:
        act = (f.get("action") or "").lower()
        if "consultar" in act:
            return f
    return forms[0] if forms else None


def _apply_submit_control(form, data: dict[str, str]) -> None:
    """ASP clássico: submit; ImageButton exige name.x e name.y no POST."""
    for sub in form.find_all("input", attrs={"type": "submit"}):
        name = sub.get("name")
        if name:
            data[name] = sub.get("value") or "Consultar"
            return
    for sub in form.find_all("input", attrs={"type": "image"}):
        name = sub.get("name")
        if name:
            data[f"{name}.x"] = "5"
            data[f"{name}.y"] = "5"
            return
    for sub in form.find_all("button", attrs={"type": "submit"}):
        name = sub.get("name")
        if name:
            inner = sub.get_text(strip=True) or sub.get("value") or ""
            data[name] = inner or "Enviar"
            return


def _decompose_noise_tags(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()


def _parse_colon_key_value_lines(html: str) -> dict[str, str]:
    """Pares \"Rótulo: valor\" por linha (mensagens ASP, divs, parágrafos)."""
    soup = BeautifulSoup(html, "lxml")
    _decompose_noise_tags(soup)
    text = soup.get_text("\n", strip=True)
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 400:
            continue
        m = _COLON_PAIR.match(line)
        if not m:
            continue
        key = m.group(1).strip().rstrip(":")
        val = m.group(2).strip()
        if not key or not val or len(key) < 2 or len(key) > 140:
            continue
        if "http" in key.lower() or "://" in key:
            continue
        if len(key) > 72:
            continue
        if sum(1 for c in key if c.isalpha()) < 2:
            continue
        if re.fullmatch(r"[\d\s\./:\-]+", key):
            continue
        if key not in out:
            out[key] = val
    return out


def fallback_visible_text(html: str) -> str:
    """Texto visível aproximado; ignora scripts e, se preciso, remove tags do HTML bruto."""
    soup = BeautifulSoup(html, "lxml")
    _decompose_noise_tags(soup)
    plain = soup.get_text(" ", strip=True)
    if plain:
        return plain
    stripped = re.sub(r"<[^>]+>", " ", html, flags=re.I)
    return re.sub(r"\s+", " ", stripped).strip()


def _parse_atividade_principal(soup: BeautifulSoup) -> str | None:
    """Bloco com <strong>Atividade Principal</strong> dentro de span.label_text; valor no próximo span.label_text."""
    for strong in soup.find_all("strong"):
        if strong.get_text(strip=True) != "Atividade Principal":
            continue
        wrap = strong.find_parent("span", class_=lambda c: bool(c) and "label_text" in c)
        if wrap is None:
            continue
        cur = wrap.next_sibling
        while cur is not None:
            if getattr(cur, "name", None) == "span" and cur.get("class") and "label_text" in cur.get("class", []):
                txt = cur.get_text(" ", strip=True)
                return txt or None
            cur = cur.next_sibling
    return None


def _parse_label_title_blocks(soup: BeautifulSoup) -> dict[str, str]:
    """Layout Sintegra GO (Bootstrap): ``span/div.label_title`` + ``span.label_text``."""
    out: dict[str, str] = {}

    for title in soup.find_all(class_=lambda c: bool(c) and "label_title" in c):
        if title.name not in ("span", "div"):
            continue
        key = re.sub(r"\s+", " ", title.get_text(" ", strip=True)).strip().rstrip(":").strip()
        if not key or len(key) > 200:
            continue
        val_el = title.find_next_sibling("span", class_=lambda c: bool(c) and "label_text" in c)
        if val_el is None:
            continue
        val = val_el.get_text(" ", strip=True)
        if val:
            out[key] = val

    ap = _parse_atividade_principal(soup)
    if ap:
        out["Atividade Principal"] = ap
    return out


CONTRIBUINTE_CAMPOS: tuple[str, ...] = (
    "CNPJ",
    "Inscrição Estadual",
    "Cadastro Atualizado em",
    "Nome Empresarial",
    "Contribuinte?",
    "Nome Fantasia",
    "Endereço Estabelecimento",
    "Atividade Principal",
)


def _norm_label_key(s: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(s).lower()).strip()


def _contribuinte_view(flat: dict[str, str]) -> dict[str, str]:
    """Chaves fixas para o cliente (ex.: GET /results), com correspondência acento-insensível."""
    by_norm = {_norm_label_key(k): (k, v) for k, v in flat.items()}
    princ: dict[str, str] = {}
    for canon in CONTRIBUINTE_CAMPOS:
        if canon in flat and flat[canon].strip():
            princ[canon] = flat[canon].strip()
            continue
        hit = by_norm.get(_norm_label_key(canon))
        if hit and str(hit[1]).strip():
            princ[canon] = str(hit[1]).strip()
            continue
        princ[canon] = ""
    return princ


def _attach_contribuinte(fields: dict[str, str]) -> dict[str, Any]:
    """Evita repetir os 8 campos principais no nível raiz do JSON."""
    princ = _contribuinte_view(fields)
    principal_norms = {_norm_label_key(c) for c in CONTRIBUINTE_CAMPOS}
    extras: dict[str, str] = {}
    for k, v in fields.items():
        if _norm_label_key(k) in principal_norms:
            continue
        vs = str(v).strip()
        if not vs:
            continue
        extras[k] = vs
    return {"contribuinte": princ, "campos_adicionais": extras}


def parse_labeled_fields(html: str) -> dict[str, str]:
    """Extrai pares rótulo/valor de tabelas, listas de definição e layout label_title/label_text (GO)."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            key = cells[0].get_text(" ", strip=True).strip().rstrip(":")
            val = cells[1].get_text(" ", strip=True)
            if key and val and len(key) < 160:
                out[key] = val

    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = dt.get_text(" ", strip=True).strip().rstrip(":")
            val = dd.get_text(" ", strip=True)
            if key and val:
                out[key] = val

    for k, v in _parse_label_title_blocks(soup).items():
        if v:
            out[k] = v

    for k, v in _parse_colon_key_value_lines(html).items():
        if k not in out:
            out[k] = v

    return out


def _pick_cnpj_radio(form) -> tuple[str, str] | None:
    """Escolhe o <input type=radio> de CNPJ (valor textual 'CNPJ' ou id com 'cnpj').

    No Sintegra GO atual, ``rTipoDoc`` usa valores numéricos: 1=CCE, 2=CNPJ, 3=CPF.
    """
    radios = form.find_all("input", attrs={"type": "radio"})
    groups: dict[str, list] = {}
    for r in radios:
        name = r.get("name")
        if not name:
            continue
        groups.setdefault(name, []).append(r)

    for name, group in groups.items():
        for r in group:
            blob = f"{r.get('value') or ''} {r.get('id') or ''}".lower()
            parent_txt = (r.parent.get_text(" ", strip=True) if r.parent else "").lower()
            if "cnpj" in blob or "cnpj" in parent_txt:
                return name, r.get("value") or ""
    return None


def _ensure_rtipodoc_cnpj_go(form, data: dict[str, str]) -> None:
    """Garante ``rTipoDoc=2`` (CNPJ) no formulário GO onde 1=CCE, 2=CNPJ, 3=CPF.

    O HTML vem com CCE (1) marcado; se a heurística de ``_pick_cnpj_radio`` falhar,
    o POST continuaria com ``1`` e a consulta não seria por CNPJ.
    """
    radios = [
        r for r in form.find_all("input", attrs={"type": "radio"}) if r.get("name") == "rTipoDoc"
    ]
    if not radios:
        return
    vals = {(r.get("value") or "").strip() for r in radios}
    if {"1", "2", "3"}.issubset(vals):
        data["rTipoDoc"] = "2"
        return
    for r in radios:
        if (r.get("value") or "").strip() == "2" and "cnpj" in f"{r.get('id') or ''}".lower():
            data["rTipoDoc"] = "2"
            return


def _pick_document_text_input(form) -> dict | None:
    candidates = [
        inp
        for inp in form.find_all("input")
        if (inp.get("type") or "text").lower() in ("text", "tel", "number", "")
        and inp.get("name")
    ]
    best = None
    best_score = -10_000
    for inp in candidates:
        if (inp.get("component") or "").lower() == "captcha" or inp.get("captchacount"):
            continue
        nm = f"{inp.get('name') or ''} {inp.get('id') or ''}".lower()
        if any(x in nm for x in ("captcha", "token", "security")):
            continue
        score = 0
        if any(k in nm for k in ("cnpj", "cpf", "documento", "nro", "numero", "nr")):
            score += 30
        if any(k in nm for k in ("inscr", "ie", "estadual")):
            score -= 12
        ml = inp.get("maxlength")
        if ml and str(ml).isdigit():
            lim = int(str(ml))
            if lim == 14:
                score += 25
            elif lim <= 18:
                score += 3
        if score > best_score:
            best_score = score
            best = inp
    return best


def _merge_visible_text_defaults(form, data: dict[str, str]) -> None:
    """Replica campos de texto do formulário. ASP legado costuma fazer CDbl sem checar Empty."""
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name or name in data:
            continue
        t = (inp.get("type") or "text").lower()
        if t not in ("text", "tel", "number", "email", "search"):
            continue
        blob = f"{name} {inp.get('id') or ''}".lower()
        attrs_blob = " ".join(f"{k}={v}" for k, v in inp.attrs.items()).lower()
        if any(x in blob for x in ("captcha", "token", "security")) or "captcha" in attrs_blob:
            continue
        if (inp.get("component") or "").lower() == "captcha" or inp.get("captchacount"):
            continue
        val = inp.get("value") or ""
        if not val and any(
            x in blob for x in ("txtie", "inscr", "inscricao", "nrinscr", "num_ie", "nrie")
        ):
            val = "0"
        data[name] = val


def _apply_select_cnpj(form, data: dict[str, str]) -> None:
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        for opt in sel.find_all("option"):
            blob = f"{opt.text} {opt.get('value') or ''}".lower()
            if "cnpj" in blob:
                data[name] = (opt.get("value") if opt.get("value") is not None else opt.text).strip()
                return


def collect_form_payload(form, cnpj_digits: str) -> tuple[str, dict[str, str]]:
    data: dict[str, str] = {}

    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        t = (inp.get("type") or "text").lower()
        if t == "hidden":
            data[name] = inp.get("value") or ""
        elif t == "checkbox" and inp.has_attr("checked"):
            data[name] = inp.get("value") or "on"
        elif t == "radio" and inp.has_attr("checked"):
            data[name] = inp.get("value") or ""

    for ta in form.find_all("textarea"):
        if ta.get("name"):
            data[ta["name"]] = ta.get_text() or ""

    for sel in form.find_all("select"):
        n = sel.get("name")
        if not n:
            continue
        chosen = sel.find("option", selected=True) or sel.find("option")
        if chosen is not None:
            val = chosen.get("value")
            data[n] = val if val is not None else (chosen.text or "")

    _apply_select_cnpj(form, data)

    picked_radio = _pick_cnpj_radio(form)
    if picked_radio:
        r_name, r_val = picked_radio
        data[r_name] = r_val

    _ensure_rtipodoc_cnpj_go(form, data)

    _merge_visible_text_defaults(form, data)

    doc_inp = _pick_document_text_input(form)
    if doc_inp is None:
        raise ScrapeError("Não foi possível localizar o campo de número do documento no formulário.")
    # SEFAZ-GO (classContribuinte.asp) usa CDbl no valor; máscara com pontos/barra gera erro 500.
    data[doc_inp["name"]] = cnpj_digits

    # Formulário atual (default.html): JS copia tCNPJ/tCPF/tCCE → tDoc antes do POST; sem isso tDoc fica vazio e CDbl falha.
    if form.find("input", attrs={"name": "tDoc"}):
        data["tDoc"] = cnpj_digits

    _apply_submit_control(form, data)

    action = form.get("action") or ""
    return action, data


async def run_sintegra_go_query(cnpj_digits: str, settings: Settings) -> dict[str, Any]:
    base = settings.sintegra_base_url.rstrip("/")
    path = settings.sintegra_entry_path
    if not path.startswith("/"):
        path = "/" + path
    entry_url = f"{base}{path}"

    async with httpx.AsyncClient(
        headers=DEFAULT_BROWSER_HEADERS,
        verify=settings.sintegra_verify_ssl,
        follow_redirects=True,
        timeout=settings.sintegra_timeout_seconds,
    ) as client:
        first = await client.get(entry_url)
        try:
            first.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScrapeError(f"Falha ao carregar página inicial ({exc.response.status_code}).") from exc

        first_html = _decode_response_body(first)
        soup = BeautifulSoup(first_html, "lxml")
        form = _pick_consulta_form(soup)
        if form is None:
            raise ScrapeError("Nenhum <form> encontrado na página inicial da consulta.")
        action, payload = collect_form_payload(form, cnpj_digits)
        post_url = urljoin(str(first.url), action)

        referer = str(first.url)
        parsed = urlparse(referer)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        post_headers = {
            "Referer": referer,
            "Origin": origin,
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        posted = await client.post(post_url, data=payload, headers=post_headers)
        if posted.is_error:
            err_html = _response_html(posted)
            preview = fallback_visible_text(err_html)[:1500].strip()
            detail = preview if preview else repr(err_html[:800])
            raise ScrapeError(
                f"Consulta rejeitada pelo servidor (HTTP {posted.status_code}). "
                f"URL: {posted.url!r}. Trecho: {detail}"
            )

        html_body = _response_html(posted)
        fields = parse_labeled_fields(html_body)
        if fields:
            return _attach_contribuinte(fields)

        logger.warning("Consulta retornou HTML sem tabelas rotuladas; devolvendo trecho textual.")
        plain = _maybe_fix_mojibake(fallback_visible_text(html_body))
        snippet = plain[:4000] if plain else ""
        if not snippet:
            raw_n = len(posted.content or b"")
            cl = posted.headers.get("content-length")
            final_url = str(posted.url)
            raise ScrapeError(
                "Resposta vazia ou não interpretável "
                f"(corpo bruto {raw_n} bytes, Content-Length={cl!r}, URL final={final_url!r}). "
                "Se o problema persistir, o servidor pode estar bloqueando requisições automatizadas."
            )
        if _looks_like_nao_encontrado(snippet):
            return {
                "situacao_consulta": "nao_encontrado",
                "mensagem": "Registro não encontrado na consulta pública Sintegra GO.",
                "resumo_pagina": snippet.strip(),
            }
        return {"resumo_pagina": snippet}
