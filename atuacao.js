/* Cockpit BFR — Lista de Atuação.
 *
 * Uma pilha de condições sobre as carteiras, e uma ação opcional no fim.
 * Não é gatilho: gatilho é condição permanente que vale vigiar; isto é a
 * pergunta do momento, que você faz, atua e descarta.
 *
 * Mora dentro do Radar, como um segundo modo, para não criar mais uma aba.
 */
(function () {
  "use strict";

  var MODO = "sinais";        // sinais | atuacao
  var COND = [];              // as condições empilhadas
  var ACAO = { tipo: "listar", alvo: "", ate: "" };
  var RESULTADO = null;
  var SALVAS = [];

  // ------------------------------------------------------------- catálogo

  var CAMPOS = [
    { g: "Posse",      id: "tem_ativo",     nome: "Tem o ativo",            tipo: "texto" },
    { g: "Posse",      id: "nao_tem_ativo", nome: "Não tem o ativo",        tipo: "texto" },
    { g: "Posse",      id: "tem_categoria", nome: "Tem ativo da categoria", tipo: "categoria" },
    { g: "Alocação",   id: "pct_classe",    nome: "% na classe",            tipo: "num", qual: "classe" },
    { g: "Alocação",   id: "pct_ativo",     nome: "% no ativo",             tipo: "num", qual: "ativo" },
    { g: "Alocação",   id: "pct_emissor",   nome: "% no emissor",           tipo: "num", qual: "emissor" },
    { g: "Alocação",   id: "pct_indexador", nome: "% no indexador",         tipo: "num", qual: "indexador" },
    { g: "Liquidez",   id: "caixa",         nome: "Caixa líquido",          tipo: "num" },
    { g: "Liquidez",   id: "folga",         nome: "Folga sobre a reserva",  tipo: "num" },
    { g: "Liquidez",   id: "credito_vence", nome: "Crédito vencendo em",    tipo: "num" },
    { g: "Desempenho", id: "pcdi_12m",      nome: "% do CDI em 12M",        tipo: "num" },
    { g: "Desempenho", id: "rent_ano",      nome: "Rentabilidade no ano",   tipo: "num" },
    { g: "Desempenho", id: "em_perda",      nome: "Posições em perda",      tipo: "num" },
    { g: "Carteira",   id: "patrimonio",    nome: "Patrimônio",             tipo: "num" },
    { g: "Carteira",   id: "segmento",      nome: "Segmento",               tipo: "texto" },
    { g: "Carteira",   id: "estruturadas",  nome: "Já opera estruturadas",  tipo: "bool" }
  ];

  var OPS_NUM = [["<", "menor que"], [">", "maior que"], ["<=", "até"], [">=", "a partir de"]];
  var OPS_TXT = [["contem", "contém"], ["igual", "é"]];
  var OPS_BOOL = [["sim", "sim"], ["nao", "não"]];

  /* Categorias de ativo. A de liquidez usa o funcaoDe() que o cockpit já tem —
   * é a mesma régua que separa caixa de verdade de crédito com prazo. As
   * outras são por nome, e vão virar uma tela de edição quando você quiser. */
  var CATEGORIAS = {
    "Liquidez (Selic, DI, referenciado)": function (p) { return fx(p) === "liquido"; },
    "Crédito com prazo":                  function (p) { return fx(p) === "credito"; },
    "Ilíquido":                           function (p) { return fx(p) === "iliquido"; },
    "Fundos imobiliários":                function (p) { return /\bFII\b|Imobili/i.test(p.ativo || ""); },
    "Ações":                              function (p) { return /^[A-Z]{4}(3|4|5|6|11|34|35|39)\b/.test((p.ativo || "").trim()); },
    "Tesouro":                            function (p) { return /Tesouro|NTN-B|\bLFT\b|\bLTN\b/i.test(p.ativo || ""); },
    "Bancário (CDB, LCI, LCA)":           function (p) { return /\bCDB\b|\bLCI\b|\bLCA\b|\bLC\b|Letra/i.test(p.ativo || ""); },
    "Estruturadas e COE":                 function (p) { return /\bCOE\b|Estruturad/i.test(p.ativo || ""); }
  };

  function fx(p) {
    try { return window.funcaoDe ? window.funcaoDe(p.ativo, p.estrategia) : ""; }
    catch (e) { return ""; }
  }

  function campoPor(id) {
    for (var i = 0; i < CAMPOS.length; i++) if (CAMPOS[i].id === id) return CAMPOS[i];
    return CAMPOS[0];
  }

  // ------------------------------------------------------------ avaliação

  function num(v) {
    if (v == null) return 0;
    var t = String(v).replace(/[R$\s%.]/g, "").replace(",", ".");
    return parseFloat(t) || 0;
  }

  function liq(c) {
    try { return window.liqDe ? window.liqDe(c) : {}; } catch (e) { return {}; }
  }

  function pctClasse(c, nome) {
    var p = c.patrimonio || 0, alvo = (nome || "").toLowerCase(), s = 0;
    if (!p || !alvo) return 0;
    (c.posicoes || []).forEach(function (x) {
      if ((x.estrategia || "").toLowerCase().indexOf(alvo) !== -1) s += x.saldo || 0;
    });
    return s / p * 100;
  }

  function pctPorNome(c, termo) {
    var p = c.patrimonio || 0, alvo = (termo || "").toLowerCase(), s = 0;
    if (!p || !alvo) return 0;
    (c.posicoes || []).forEach(function (x) {
      if ((x.ativo || "").toLowerCase().indexOf(alvo) !== -1) s += x.saldo || 0;
    });
    return s / p * 100;
  }

  function valorDe(c, cond) {
    var L = liq(c);
    switch (cond.campo) {
      case "pct_classe":    return pctClasse(c, cond.qual);
      case "pct_ativo":
      case "pct_emissor":
      case "pct_indexador": return pctPorNome(c, cond.qual);
      case "caixa":         return L.liquido || 0;
      case "folga":         return L.reserva ? (L.folga || 0) : (L.liquido || 0);
      case "credito_vence": return L.credito || 0;
      case "pcdi_12m":      return ((c.resumo || {})["12M"] || {}).pcdi || 0;
      case "rent_ano":      return ((c.resumo || {}).ANO || {}).rent || 0;
      case "em_perda":      return (c.posicoes || []).filter(function (p) {
                              return (p.rent_ano || 0) < 0; }).length;
      case "patrimonio":    return c.patrimonio || 0;
      default:              return 0;
    }
  }

  function bate(c, cond) {
    var f = campoPor(cond.campo);

    if (f.tipo === "texto") {
      var alvo = (cond.valor || "").toLowerCase();
      if (!alvo) return true;
      if (cond.campo === "segmento")
        return (c.segmento || "").toLowerCase().indexOf(alvo) !== -1;
      var tem = (c.posicoes || []).some(function (p) {
        return (p.ativo || "").toLowerCase().indexOf(alvo) !== -1;
      });
      return cond.campo === "nao_tem_ativo" ? !tem : tem;
    }

    if (f.tipo === "categoria") {
      var teste = CATEGORIAS[cond.valor];
      if (!teste) return true;
      return (c.posicoes || []).some(function (p) { return (p.saldo || 0) > 0 && teste(p); });
    }

    if (f.tipo === "bool") {
      var opera = (c.posicoes || []).some(function (p) {
        return /\bCOE\b|Estruturad/i.test(p.ativo || "");
      });
      return cond.op === "sim" ? opera : !opera;
    }

    var v = valorDe(c, cond), alvoN = num(cond.valor);
    if (cond.op === "<") return v < alvoN;
    if (cond.op === ">") return v > alvoN;
    if (cond.op === "<=") return v <= alvoN;
    return v >= alvoN;
  }

  /* De onde o dinheiro sai: a maior posição líquida da carteira, nomeada.
   * Sem isso a lista responde "quem precisa" e deixa a parte difícil — achar
   * o recurso — para você fazer conta a conta. */
  function fonteDe(c) {
    var melhor = null;
    (c.posicoes || []).forEach(function (p) {
      if ((p.saldo || 0) <= 0 || fx(p) !== "liquido") return;
      if (!melhor || p.saldo > melhor.saldo) melhor = p;
    });
    var L = liq(c);
    return {
      ativo: melhor ? melhor.ativo : null,
      saldo: melhor ? melhor.saldo : 0,
      folga: L.reserva ? (L.folga || 0) : (L.liquido || 0),
      temReserva: !!L.reserva
    };
  }

  function montar() {
    var contas = window.contas || [];
    var linhas = [];

    contas.forEach(function (c) {
      for (var i = 0; i < COND.length; i++) if (!bate(c, COND[i])) return;

      var l = { c: c, fonte: fonteDe(c) };

      if (ACAO.tipo === "aumentar" || ACAO.tipo === "reduzir") {
        var hoje = pctClasse(c, ACAO.alvo);
        var ate = num(ACAO.ate);
        var p = c.patrimonio || 0;
        l.hoje = hoje;
        l.delta = (ate - hoje) / 100 * p;             // positivo = falta
        if (ACAO.tipo === "reduzir") l.delta = (hoje - ate) / 100 * p;
        l.cabe = ACAO.tipo === "aumentar"
          ? Math.max(0, Math.min(l.delta, l.fonte.folga))
          : Math.max(0, l.delta);
      }
      linhas.push(l);
    });

    linhas.sort(function (a, b) { return (b.cabe || b.c.patrimonio) - (a.cabe || a.c.patrimonio); });
    RESULTADO = linhas;
  }

  // ---------------------------------------------------------- persistência

  function carregarSalvas() {
    return fetch("/api/consultas", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (j) { SALVAS = j || []; })
      .catch(function () { SALVAS = []; });
  }

  function salvar(nome) {
    return fetch("/api/consultas", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nome: nome, condicoes: COND, acao: ACAO }),
      credentials: "same-origin"
    }).then(carregarSalvas);
  }

  // ----------------------------------------------------------------- tela

  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
    });
  }

  function brl(v) {
    if (v == null) return "—";
    return "R$ " + Math.round(v).toLocaleString("pt-BR");
  }

  function selCampos(sel) {
    var grupos = [], visto = {};
    CAMPOS.forEach(function (f) { if (!visto[f.g]) { visto[f.g] = 1; grupos.push(f.g); } });
    return grupos.map(function (g) {
      return '<optgroup label="' + esc(g) + '">' +
        CAMPOS.filter(function (f) { return f.g === g; }).map(function (f) {
          return '<option value="' + f.id + '"' + (f.id === sel ? " selected" : "") + '>' +
            esc(f.nome) + '</option>';
        }).join("") + '</optgroup>';
    }).join("");
  }

  function selOps(f, sel) {
    var lista = f.tipo === "num" ? OPS_NUM : f.tipo === "bool" ? OPS_BOOL : OPS_TXT;
    return lista.map(function (o) {
      return '<option value="' + o[0] + '"' + (o[0] === sel ? " selected" : "") + '>' +
        esc(o[1]) + '</option>';
    }).join("");
  }

  function linhaCond(cond, i) {
    var f = campoPor(cond.campo);
    var valor = f.tipo === "categoria"
      ? '<select class="ain" data-k="valor" data-i="' + i + '">' +
        Object.keys(CATEGORIAS).map(function (k) {
          return '<option' + (k === cond.valor ? " selected" : "") + '>' + esc(k) + '</option>';
        }).join("") + '</select>'
      : f.tipo === "bool" ? '<span style="color:#8FA3C0;font-size:12px">—</span>'
      : '<input class="ain" data-k="valor" data-i="' + i + '" value="' + esc(cond.valor || "") + '">';

    return '<div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;padding:11px;' +
      'border:1px solid #1F2E48;border-radius:9px;margin-bottom:9px">' +
      '<div style="flex:1 1 190px;min-width:0"><label class="alab">Campo</label>' +
      '<select class="ain" data-k="campo" data-i="' + i + '">' + selCampos(cond.campo) + '</select></div>' +
      (f.qual ? '<div style="flex:1 1 150px;min-width:0"><label class="alab">Qual ' + esc(f.qual) +
        '</label><input class="ain" data-k="qual" data-i="' + i + '" value="' + esc(cond.qual || "") + '"></div>' : "") +
      '<div style="flex:0 1 140px;min-width:0"><label class="alab">Condição</label>' +
      '<select class="ain" data-k="op" data-i="' + i + '">' + selOps(f, cond.op) + '</select></div>' +
      '<div style="flex:1 1 170px;min-width:0"><label class="alab">Valor</label>' + valor + '</div>' +
      '<button class="abtn" data-rm="' + i + '" aria-label="Remover condição">remover</button>' +
      '</div>';
  }

  function tabela() {
    if (!RESULTADO) return "";
    if (!RESULTADO.length)
      return '<div style="background:#111B2E;border:1px solid #1F2E48;border-radius:12px;padding:24px;' +
        'text-align:center;color:#8FA3C0;font-size:13px">Nenhuma carteira atende a todas as condições.</div>';

    var comAcao = ACAO.tipo === "aumentar" || ACAO.tipo === "reduzir";
    var totalFolga = RESULTADO.reduce(function (s, l) { return s + (l.fonte.folga || 0); }, 0);
    var totalCabe = RESULTADO.reduce(function (s, l) { return s + (l.cabe || 0); }, 0);

    var cards =
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:13px;margin-bottom:14px">' +
      card("CARTEIRAS", RESULTADO.length, "de " + (window.contas || []).length) +
      card("LIQUIDEZ ENCONTRADA", brl(totalFolga), "folga de caixa somada", true) +
      (comAcao ? card("CABE BOLETAR", brl(totalCabe), "respeitando a reserva de cada um", true) : "") +
      '</div>';

    var cab = ['Conta', 'Patrimônio', 'Onde está a liquidez'];
    if (comAcao) cab = cab.concat([ACAO.alvo + " hoje", "Falta/excede", "Cabe boletar"]);

    return cards +
      '<div style="background:#111B2E;border:1px solid #1F2E48;border-radius:12px;overflow-x:auto">' +
      '<table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:780px">' +
      '<thead><tr style="background:#0E1726;color:#8FA3C0;font-size:11px;text-align:left">' +
      cab.map(function (t, i) {
        return '<th style="padding:10px;font-weight:600' + (i > 0 ? ";text-align:right" : "") + '">' +
          esc(t) + '</th>';
      }).join("") + '</tr></thead><tbody>' +
      RESULTADO.slice(0, 60).map(function (l) {
        var f = l.fonte;
        return '<tr style="border-top:1px solid #18233A">' +
          '<td style="padding:11px 10px"><strong>' + esc(l.c.conta) + '</strong><br>' +
          '<span style="font-size:11px;color:#8FA3C0">' + esc(l.c.segmento || "—") + '</span></td>' +
          '<td style="padding:11px 10px;text-align:right">' + brl(l.c.patrimonio) + '</td>' +
          '<td style="padding:11px 10px;text-align:right">' +
          (f.ativo ? '<span style="font-size:11px;color:#6FDFAE">' + esc(f.ativo.slice(0, 34)) + '</span><br>' +
            '<span style="font-size:11px;color:#8FA3C0">' + brl(f.folga) +
            (f.temReserva ? " de folga" : " · sem reserva definida") + '</span>'
            : '<span style="font-size:11px;color:#FFB4B4">sem caixa líquido</span>') + '</td>' +
          (comAcao ? '<td style="padding:11px 10px;text-align:right;color:#8FA3C0">' +
            (l.hoje || 0).toFixed(1).replace(".", ",") + '%</td>' +
            '<td style="padding:11px 10px;text-align:right">' + brl(Math.max(l.delta, 0)) + '</td>' +
            '<td style="padding:11px 10px;text-align:right;color:#F5C044;font-weight:600">' +
            (l.cabe > 0 ? brl(l.cabe) : "—") + '</td>' : "") +
          '</tr>';
      }).join("") +
      '</tbody></table></div>' +
      (RESULTADO.length > 60 ? '<div style="font-size:11.5px;color:#8FA3C0;margin-top:9px">' +
        'Mostrando as 60 primeiras de ' + RESULTADO.length + '.</div>' : "");
  }

  function card(rotulo, valor, nota, ouro) {
    return '<div style="background:#111B2E;border:1px solid #1F2E48;border-radius:11px;padding:14px">' +
      '<div style="font-size:11px;color:#8FA3C0;letter-spacing:.04em">' + rotulo + '</div>' +
      '<div style="font-size:23px;font-weight:600;margin-top:6px' + (ouro ? ";color:#F5C044" : "") + '">' +
      valor + '</div>' +
      '<div style="font-size:11.5px;color:#8FA3C0;margin-top:3px">' + nota + '</div></div>';
  }

  function tela() {
    return '<style>' +
      '.alab{font-size:11px;color:#8FA3C0;display:block}' +
      '.ain{width:100%;box-sizing:border-box;margin-top:5px;padding:9px 10px;background:#0B1220;' +
      'color:#E6EDF7;border:1px solid #1F2E48;border-radius:7px;font-size:12.5px;font-family:inherit}' +
      '.abtn{background:transparent;border:1px solid #1F2E48;color:#8FA3C0;border-radius:7px;' +
      'padding:9px 13px;font-size:12px;cursor:pointer;font-family:inherit}' +
      '.abtn:hover{border-color:#F5C044;color:#F5C044}' +
      '.apri{background:#F5C044;border:none;color:#0B1220;border-radius:7px;padding:10px 17px;' +
      'font-size:12.5px;font-weight:700;cursor:pointer;font-family:inherit}' +
      '</style>' +

      '<section style="background:#111B2E;border:1px solid #1F2E48;border-radius:12px;padding:16px;margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;gap:10px;margin-bottom:13px;flex-wrap:wrap">' +
      '<span style="font-size:12.5px;font-weight:600">Mostre as carteiras em que</span>' +
      '<span style="font-size:11.5px;color:#8FA3C0">todas as condições valem ao mesmo tempo</span>' +
      (SALVAS.length ? '<span style="flex:1"></span><select id="a_salvas" class="ain" style="width:auto;margin:0">' +
        '<option value="">listas salvas…</option>' +
        SALVAS.map(function (s) { return '<option value="' + s.id + '">' + esc(s.nome) + '</option>'; }).join("") +
        '</select>' : "") +
      '</div>' +

      COND.map(linhaCond).join("") +

      '<button id="a_add" style="background:transparent;border:1px dashed #2A3C5C;color:#8FA3C0;' +
      'border-radius:8px;padding:11px 16px;font-size:12.5px;cursor:pointer;font-family:inherit;width:100%">' +
      '+ adicionar condição</button>' +

      '<hr style="border:none;border-top:1px solid #1F2E48;margin:16px 0">' +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:13px">' +
      '<div><label class="alab" for="a_tipo">E então (opcional)</label>' +
      '<select id="a_tipo" class="ain">' +
      ['listar|só listar', 'aumentar|aumentar uma classe', 'reduzir|reduzir uma classe'].map(function (o) {
        var p = o.split("|");
        return '<option value="' + p[0] + '"' + (ACAO.tipo === p[0] ? " selected" : "") + '>' + p[1] + '</option>';
      }).join("") + '</select></div>' +
      '<div><label class="alab" for="a_alvo">O quê</label>' +
      '<input id="a_alvo" class="ain" value="' + esc(ACAO.alvo) + '" placeholder="ex: Inflação"></div>' +
      '<div><label class="alab" for="a_ate">Até</label>' +
      '<input id="a_ate" class="ain" value="' + esc(ACAO.ate) + '" placeholder="ex: 15%"></div>' +
      '</div>' +

      '<div style="display:flex;gap:10px;margin-top:15px;flex-wrap:wrap">' +
      '<button id="a_montar" class="apri">Montar lista</button>' +
      '<button id="a_salvar" class="abtn">Salvar esta consulta</button>' +
      '<button id="a_limpar" class="abtn">Limpar</button>' +
      '</div></section>' +

      tabela();
  }

  // -------------------------------------------------------------- eventos

  function ligar(raiz) {
    raiz.querySelectorAll("[data-k]").forEach(function (el) {
      el.onchange = function () {
        var i = +el.getAttribute("data-i"), k = el.getAttribute("data-k");
        COND[i][k] = el.value;
        if (k === "campo") {                       // o campo mudou: operador volta ao padrão
          var f = campoPor(el.value);
          COND[i].op = f.tipo === "num" ? "<" : f.tipo === "bool" ? "sim" : "contem";
          if (f.tipo === "categoria") COND[i].valor = Object.keys(CATEGORIAS)[0];
        }
        desenhar();
      };
    });

    raiz.querySelectorAll("[data-rm]").forEach(function (b) {
      b.onclick = function () { COND.splice(+b.getAttribute("data-rm"), 1); desenhar(); };
    });

    var add = raiz.querySelector("#a_add");
    if (add) add.onclick = function () {
      COND.push({ campo: "tem_categoria", op: "contem",
                  valor: Object.keys(CATEGORIAS)[0], qual: "" });
      desenhar();
    };

    var montarBt = raiz.querySelector("#a_montar");
    if (montarBt) montarBt.onclick = function () {
      ACAO.tipo = raiz.querySelector("#a_tipo").value;
      ACAO.alvo = raiz.querySelector("#a_alvo").value;
      ACAO.ate = raiz.querySelector("#a_ate").value;
      montar();
      desenhar();
    };

    var limpar = raiz.querySelector("#a_limpar");
    if (limpar) limpar.onclick = function () {
      COND = []; RESULTADO = null; ACAO = { tipo: "listar", alvo: "", ate: "" }; desenhar();
    };

    var salvarBt = raiz.querySelector("#a_salvar");
    if (salvarBt) salvarBt.onclick = function () {
      var nome = prompt("Nome desta consulta:");
      if (!nome) return;
      salvar(nome).then(desenhar);
    };

    var sel = raiz.querySelector("#a_salvas");
    if (sel) sel.onchange = function () {
      var s = SALVAS.filter(function (x) { return String(x.id) === sel.value; })[0];
      if (!s) return;
      COND = JSON.parse(JSON.stringify(s.condicoes || []));
      ACAO = Object.assign({ tipo: "listar", alvo: "", ate: "" }, s.acao || {});
      montar(); desenhar();
    };
  }

  // --------------------------------------------------- encaixe no Radar

  function barra() {
    return '<div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">' +
      '<button id="m_sinais" style="background:' + (MODO === "sinais" ? "#1A2742" : "#111B2E") +
      ';border:1px solid ' + (MODO === "sinais" ? "#F5C044" : "#1F2E48") + ';color:' +
      (MODO === "sinais" ? "#F5C044" : "#8FA3C0") + ';border-radius:9px;padding:9px 15px;' +
      'font-size:12.5px;cursor:pointer;font-family:inherit">Sinais do Radar</button>' +
      '<button id="m_atuacao" style="background:' + (MODO === "atuacao" ? "#1A2742" : "#111B2E") +
      ';border:1px solid ' + (MODO === "atuacao" ? "#F5C044" : "#1F2E48") + ';color:' +
      (MODO === "atuacao" ? "#F5C044" : "#8FA3C0") + ';border-radius:9px;padding:9px 15px;' +
      'font-size:12.5px;cursor:pointer;font-family:inherit">Lista de Atuação</button>' +
      '</div>';
  }

  function desenhar() {
    var view = document.getElementById("view");
    if (!view) return;

    var antiga = document.getElementById("a_raiz");
    if (antiga) antiga.remove();

    var caixa = document.createElement("div");
    caixa.id = "a_raiz";
    caixa.innerHTML = barra() + (MODO === "atuacao" ? tela() : "");
    view.insertBefore(caixa, view.firstChild);

    // no modo atuação o Radar sai de cena; no modo sinais, volta
    [].slice.call(view.children).forEach(function (el) {
      if (el !== caixa && el.id !== "g_painel") el.style.display = MODO === "atuacao" ? "none" : "";
    });

    caixa.querySelector("#m_sinais").onclick = function () { MODO = "sinais"; desenhar(); };
    caixa.querySelector("#m_atuacao").onclick = function () { MODO = "atuacao"; desenhar(); };
    if (MODO === "atuacao") ligar(caixa);
  }

  function instalar() {
    var renderOriginal = window.render;
    if (typeof renderOriginal !== "function") return;

    window.render = function (v) {
      var saida = renderOriginal(v);
      if (v === "ops") setTimeout(desenhar, 0);
      return saida;
    };

    carregarSalvas();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", instalar);
  } else {
    instalar();
  }
})();
