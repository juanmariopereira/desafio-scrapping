from pathlib import Path

from bs4 import BeautifulSoup

from scraper.sintegra_go import (
    _attach_contribuinte,
    _looks_like_nao_encontrado,
    _maybe_fix_mojibake,
    _pick_consulta_form,
    collect_form_payload,
    fallback_visible_text,
    parse_labeled_fields,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_bootstrap_go_layout_and_contribuinte() -> None:
    html = (FIXTURES / "sintegra_result_bootstrap_go.html").read_text(encoding="utf-8")
    fields = parse_labeled_fields(html)
    assert fields["CNPJ"] == "02.656.759/0001-52"
    assert fields.get("Inscrição Estadual") == "10.221.690-8"
    assert fields["Nome Empresarial"] == "GOIAS ORDEM DOS ADV DO BRASIL SEC DE GOIAS"
    assert fields["Contribuinte?"] == "Não"
    assert "GOIAS" in fields["Endereço Estabelecimento"]
    assert "6911701" in fields["Atividade Principal"]

    out = _attach_contribuinte(fields)
    assert "CNPJ" not in out
    assert "campos_adicionais" in out
    c = out["contribuinte"]
    assert c["CNPJ"] == "02.656.759/0001-52"
    assert c["Inscrição Estadual"] == "10.221.690-8"
    assert c["Cadastro Atualizado em"] == "16/01/2009"
    assert c["Nome Empresarial"] == "GOIAS ORDEM DOS ADV DO BRASIL SEC DE GOIAS"
    assert c["Contribuinte?"] == "Não"
    assert c["Nome Fantasia"] == "GOIAS OAB GABINETE DO PRESIDENTE"
    assert "GOIANIA" in c["Endereço Estabelecimento"]
    assert "6911701" in c["Atividade Principal"]


def test_maybe_fix_mojibake_nao_encontrado() -> None:
    assert _maybe_fix_mojibake("nÃ£o encontrado!") == "não encontrado!"


def test_looks_like_nao_encontrado() -> None:
    assert _looks_like_nao_encontrado("Não encontrado!")
    assert _looks_like_nao_encontrado("nÃ£o encontrado!")
    assert not _looks_like_nao_encontrado("Empresa ATIVA na base")


def test_collect_form_payload_selects_cnpj_and_document_field() -> None:
    html = (FIXTURES / "sintegra_form.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    assert form is not None
    action, data = collect_form_payload(form, "00006486000175")
    assert action.endswith("resultado-mock.asp")
    assert data["__VIEWSTATE"] == "VIEWSTATE123"
    assert data["rblDoc"] == "CNPJ"
    assert data["txtNro"] == "00006486000175"
    assert data["txtIE"] == "0"
    assert data["btnConsultar"] == "Consultar"


def test_parse_labeled_fields_reads_table() -> None:
    html = (FIXTURES / "sintegra_result.html").read_text(encoding="utf-8")
    fields = parse_labeled_fields(html)
    assert fields["CNPJ"] == "00.006.486/0001-75"
    assert fields["Razão Social"] == "EMPRESA EXEMPLO S/A"
    assert fields["Situação Cadastral"] == "Ativo"


def test_parse_labeled_fields_reads_colon_lines_without_table() -> None:
    html = (FIXTURES / "sintegra_result_colon_lines.html").read_text(encoding="utf-8")
    fields = parse_labeled_fields(html)
    assert fields["CNPJ"] == "00.000.000/0001-91"
    assert fields["Razão Social"] == "EMPRESA EM LINHAS LTDA"


def test_collect_form_payload_digits_only_even_when_maxlength_18() -> None:
    """Campo maxlength 18 é só UI; o servidor GO usa CDbl e exige 14 dígitos sem máscara."""
    html = """<!DOCTYPE html><html><body><form action="/consultar.asp" method="post">
    <input type="hidden" name="__VIEWSTATE" value="v" />
    <input type="radio" name="rblDoc" value="CNPJ" />
    <input type="text" name="txtNro" maxlength="18" />
    <input type="submit" name="btn" value="Ir" />
    </form></body></html>"""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    assert form is not None
    _, data = collect_form_payload(form, "00006486000175")
    assert data["txtNro"] == "00006486000175"


def test_collect_form_payload_image_submit_posts_coordinates() -> None:
    html = """<!DOCTYPE html><html><body><form action="consultar.asp" method="post">
    <input type="hidden" name="__VIEWSTATE" value="v" />
    <input type="radio" name="rblDoc" value="CNPJ" />
    <input type="text" name="txtNro" maxlength="14" />
    <input type="image" name="imgBtn" src="a.png" alt="Consultar" />
    </form></body></html>"""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    assert form is not None
    _, data = collect_form_payload(form, "00006486000175")
    assert data["imgBtn.x"] == "5"
    assert data["imgBtn.y"] == "5"


def test_collect_form_payload_forces_rtipodoc_2_when_go_numeric_group_even_without_cnpj_id() -> None:
    """GO: 1=CCE, 2=CNPJ, 3=CPF. Sem 'cnpj' no id, o picker não marca o radio; ainda assim deve enviar 2."""
    html = """<!DOCTYPE html><html><body><form action="consultar.asp" method="post">
    <input type="radio" name="rTipoDoc" value="1" checked="checked" />
    <input type="radio" name="rTipoDoc" value="2" />
    <input type="radio" name="rTipoDoc" value="3" />
    <input type="text" name="tDoc" value="" />
    <input type="text" name="tCNPJ" value="" />
    <input type="submit" name="btCGC" value="Consultar" />
    </form></body></html>"""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    assert form is not None
    _, data = collect_form_payload(form, "49237112000150")
    assert data["rTipoDoc"] == "2"


def test_collect_form_payload_syncs_tdoc_with_tcnpj_go_form() -> None:
    """Formulário real GO: tDoc espelha tCNPJ via JS; o POST deve enviar ambos."""
    html = """<!DOCTYPE html><html><body><form action="consultar.asp" method="post">
    <input type="hidden" name="__VIEWSTATE" value="v" />
    <input type="radio" name="rTipoDoc" value="1" />
    <input type="radio" name="rTipoDoc" value="2" id="rTipoDocCNPJ" />
    <input type="radio" name="rTipoDoc" value="3" />
    <input type="text" name="tDoc" value="" />
    <input type="text" name="tCNPJ" value="" />
    <input type="text" name="tCCE" value="" />
    <input type="submit" name="btCGC" value="Consultar" />
    </form></body></html>"""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    assert form is not None
    _, data = collect_form_payload(form, "49237112000150")
    assert data["rTipoDoc"] == "2"
    assert data["tCNPJ"] == "49237112000150"
    assert data["tDoc"] == "49237112000150"


def test_pick_document_skips_captcha_component() -> None:
    html = """<form action="consultar.asp">
    <input type="hidden" name="__VIEWSTATE" value="x" />
    <input type="radio" name="rTipoDoc" value="2" id="rTipoDocCNPJ" />
    <input type="text" name="tTest" id="tTest" component="captcha" captchaCount="4" />
    <input type="text" name="tCNPJ" id="tCNPJ" />
    <input type="submit" name="btCGC" value="ok" />
    </form>"""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    assert form is not None
    _, data = collect_form_payload(form, "00006486000175")
    assert "tTest" not in data
    assert data["tCNPJ"] == "00006486000175"


def test_pick_consulta_form_prefers_action_with_consultar() -> None:
    html = """<html><body>
    <form action="/other.asp"><input name="q" /></form>
    <form action="/Sintegra/Consulta/consultar.asp">
    <input type="hidden" name="__VIEWSTATE" value="z" />
    </form>
    </body></html>"""
    soup = BeautifulSoup(html, "lxml")
    f = _pick_consulta_form(soup)
    assert f is not None
    assert "consultar" in (f.get("action") or "").lower()


def test_fallback_visible_text_ignores_scripts() -> None:
    html = (
        "<html><body><script>long noise "
        + "x" * 400
        + "</script><p>Sintegra GO</p></body></html>"
    )
    out = fallback_visible_text(html)
    assert "Sintegra GO" in out
    assert "long noise" not in out
