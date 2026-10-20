/* Cockpit BFR — botão de atualizar carteiras.
 *
 * Injetado na página, sem tocar no cockpit.html. Abre um painel onde você
 * joga os relatórios do XPerformance, acompanha a leitura e recarrega.
 *
 * Por que recarregar resolve tudo: os dados das carteiras são injetados pelo
 * servidor a cada acesso. Então, no instante em que o lote entra no banco, um
 * reload atualiza o cockpit inteiro de uma vez — KPIs, gráficos, estratégias,
 * posições, baskets, simulação. Não existe tela que fique para trás.
 */
(function () {
  "use strict";

  var ACEITA = ".pdf,.zip,.xlsx,.xls,.csv";
  var selecionados = [];
  var lote = null;
  var timer = null;

  // --------------------------------------------------------------- estilo
  var css = document.createElement("style");
  css.textContent = [
    "#bfrUpBtn{border-radius:8px;border:1px solid #F5C044;background:#F5C044;color:#0B1220;",
    "padding:6px 14px;font-size:11.5px;font-weight:700;cursor:pointer;font-family:inherit;",
    "white-space:nowrap}",
    "#bfrUpBtn:hover{filter:brightness(1.08)}",
    "#bfrUpBtn.solto{position:fixed;right:14px;z-index:9998;padding:8px 14px;font-size:13px;",
    "top:calc(10px + env(safe-area-inset-top,0px));box-shadow:0 2px 10px rgba(0,0,0,.4)}",
    "#bfrUpOv{position:fixed;inset:0;z-index:9999;background:rgba(5,9,17,.72);",
    "display:none;align-items:center;justify-content:center;padding:20px}",
    "#bfrUpOv.on{display:flex}",
    "#bfrUpBox{width:100%;max-width:560px;max-height:86vh;overflow:auto;background:#111B2E;",
    "border:1px solid #1F2E48;border-radius:14px;padding:22px;color:#E6EDF7;",
    "font:14px ui-sans-serif,system-ui,sans-serif}",
    "#bfrUpBox h2{margin:0 0 4px;font-size:17px;font-weight:600}",
    "#bfrUpBox .sub{color:#8FA3C0;font-size:13px;margin-bottom:18px;line-height:1.6}",
    "#bfrDrop{border:1.5px dashed #2A3C5C;border-radius:11px;padding:26px 16px;text-align:center;",
    "color:#8FA3C0;cursor:pointer;transition:border-color .15s,background .15s}",
    "#bfrDrop:hover,#bfrDrop.over{border-color:#F5C044;background:rgba(245,192,68,.05)}",
    "#bfrLista{margin:14px 0 0;max-height:150px;overflow:auto;font-size:12.5px;color:#8FA3C0}",
    "#bfrLista div{padding:3px 0;border-bottom:1px solid #18233A}",
    "#bfrAcoes{display:flex;gap:10px;margin-top:18px}",
    "#bfrUpBox button.pri{background:#F5C044;color:#0B1220;border:none;border-radius:8px;",
    "padding:10px 16px;font-weight:600;font-size:14px;cursor:pointer}",
    "#bfrUpBox button.sec{background:transparent;color:#8FA3C0;border:1px solid #1F2E48;",
    "border-radius:8px;padding:10px 16px;font-size:14px;cursor:pointer}",
    "#bfrUpBox button:disabled{opacity:.45;cursor:default}",
    "#bfrStatus{margin-top:18px;font-size:13px;line-height:1.75;white-space:pre-wrap}",
    "#bfrBarra{height:6px;background:#0B1220;border-radius:3px;overflow:hidden;margin:12px 0 6px;display:none}",
    "#bfrBarra i{display:block;height:100%;width:0;background:#F5C044;transition:width .3s}",
    ".bfrOk{color:#3DDC97}.bfrErr{color:#FF6B6B}.bfrAviso{color:#F5C044}"
  ].join("");
  document.head.appendChild(css);

  // --------------------------------------------------------------- markup
  var botao = document.createElement("button");
  botao.id = "bfrUpBtn";
  botao.textContent = "↑ Enviar relatórios";
  botao.title = "Subir os XPerformance do lote e atualizar as carteiras";

  /* O lugar certo é a própria barra do cabeçalho, ao lado de Backup e
   * Restaurar — flutuando por cima da tela ele colidia com os selos de data.
   * A âncora é o botão de Backup: achando ele, acha-se o contêiner. */
  function barraDoCabecalho() {
    var botoes = document.querySelectorAll("header button, header label");
    for (var i = 0; i < botoes.length; i++) {
      if (/Backup|Restaurar/i.test(botoes[i].textContent || "")) {
        return botoes[i].parentElement;
      }
    }
    return null;
  }

  var overlay = document.createElement("div");
  overlay.id = "bfrUpOv";
  overlay.innerHTML =
    '<div id="bfrUpBox">' +
      '<h2>Atualizar carteiras</h2>' +
      '<p class="sub">Solte aqui os XPerformance em PDF, ou um .zip com todos eles ' +
      'de uma vez. Planilhas .xlsx são guardadas junto para complemento.</p>' +
      '<div id="bfrDrop">Clique para escolher, ou arraste os arquivos até aqui<br>' +
      '<span style="font-size:12px;opacity:.75">PDF · ZIP · XLSX</span></div>' +
      '<input type="file" id="bfrFile" multiple accept="' + ACEITA + '" style="display:none">' +
      '<div id="bfrLista"></div>' +
      '<div id="bfrBarra"><i></i></div>' +
      '<div id="bfrStatus"></div>' +
      '<div id="bfrAcoes">' +
        '<button class="pri" id="bfrEnviar" disabled>Enviar e atualizar</button>' +
        '<button class="sec" id="bfrFechar">Fechar</button>' +
      '</div>' +
    '</div>';

  function montar() {
    var barra = barraDoCabecalho();
    if (barra) {
      barra.appendChild(botao);          // na linha do Backup e Restaurar
    } else {
      botao.className = "solto";         // cabeçalho mudou: volta a flutuar
      document.body.appendChild(botao);
    }
    document.body.appendChild(overlay);

    var drop = document.getElementById("bfrDrop");
    var input = document.getElementById("bfrFile");

    botao.onclick = function () { overlay.classList.add("on"); };
    document.getElementById("bfrFechar").onclick = fechar;
    overlay.onclick = function (e) { if (e.target === overlay) fechar(); };

    drop.onclick = function () { input.click(); };
    input.onchange = function () { adicionar(input.files); };

    ["dragenter", "dragover"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) {
        e.preventDefault(); drop.classList.add("over");
      });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) {
        e.preventDefault(); drop.classList.remove("over");
      });
    });
    drop.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files) adicionar(e.dataTransfer.files);
    });

    document.getElementById("bfrEnviar").onclick = enviar;
  }

  function fechar() {
    overlay.classList.remove("on");
    if (timer) { clearInterval(timer); timer = null; }
  }

  function adicionar(lista) {
    var rars = [];
    for (var i = 0; i < lista.length; i++) {
      var f = lista[i];
      if (/\.rar$/i.test(f.name)) { rars.push(f.name); continue; }
      selecionados.push(f);
    }
    desenharLista(rars);
  }

  function desenharLista(rars) {
    var el = document.getElementById("bfrLista");
    var html = selecionados.map(function (f) {
      return "<div>" + f.name + " · " + (f.size / 1024 / 1024).toFixed(1) + " MB</div>";
    }).join("");

    if (rars && rars.length) {
      html = '<div class="bfrErr">' + rars.length + ' arquivo(s) .rar ignorado(s). ' +
        'O servidor não abre .rar — no Windows, clique com o botão direito na pasta ' +
        'e escolha "Compactar em arquivo ZIP".</div>' + html;
    }
    el.innerHTML = html;
    document.getElementById("bfrEnviar").disabled = selecionados.length === 0;
  }

  function status(texto, classe) {
    var el = document.getElementById("bfrStatus");
    el.className = classe || "";
    el.textContent = texto;
  }

  function barra(pct) {
    var b = document.getElementById("bfrBarra");
    b.style.display = pct === null ? "none" : "block";
    if (pct !== null) b.querySelector("i").style.width = pct + "%";
  }

  function enviar() {
    var corpo = new FormData();
    selecionados.forEach(function (f) { corpo.append("arquivos", f); });

    document.getElementById("bfrEnviar").disabled = true;
    status("Enviando " + selecionados.length + " arquivo(s)…");
    barra(2);

    fetch("/api/lote", { method: "POST", body: corpo, credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (j) { throw new Error(j.detail || r.status); });
        return r.json();
      })
      .then(function (j) {
        lote = j.lote_id;
        status("Lendo os relatórios…");
        timer = setInterval(acompanhar, 2000);
      })
      .catch(function (e) {
        barra(null);
        status("Não deu para enviar: " + e.message, "bfrErr");
        document.getElementById("bfrEnviar").disabled = false;
      });
  }

  function acompanhar() {
    fetch("/api/lote/" + lote, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rel = j.relatorio || {};
        if (j.status === "processando") {
          if (rel.total) {
            barra(Math.round(rel.atual / rel.total * 100));
            status("Lendo " + rel.atual + " de " + rel.total + "\n" + (rel.arquivo || ""));
          }
          return;
        }
        clearInterval(timer); timer = null;
        barra(null);
        j.status === "ok" ? concluir(j, rel) : recusar(rel);
      })
      .catch(function () { /* rede instável: a próxima volta resolve */ });
  }

  function concluir(j, rel) {
    var linhas = [];
    linhas.push(rel.lidos + " carteira(s) atualizada(s) · AUM " + (rel.aum_formatado || "—"));
    if (rel.meses_historico) linhas.push(rel.meses_historico + " meses de histórico gravados");
    if (rel.novas && rel.novas.length) linhas.push("Contas novas: " + rel.novas.join(", "));
    if (rel.ausentes && rel.ausentes.length)
      linhas.push("Não vieram neste lote: " + rel.ausentes.join(", "));
    if (rel.planilhas && rel.planilhas.length)
      linhas.push("Planilhas guardadas (ainda não interpretadas): " + rel.planilhas.join(", "));
    (rel.saltos || []).forEach(function (s) {
      linhas.push("Conta " + s.conta + ": variação de " + s.variacao + "% — confira");
    });
    (rel.falhas || []).forEach(function (f) {
      linhas.push("Falhou: " + f.arquivo + " — " + f.erro);
    });
    (rel.erros_pacote || []).forEach(function (f) {
      linhas.push(f.arquivo + " — " + f.erro);
    });

    status(linhas.join("\n"), rel.saltos && rel.saltos.length ? "bfrAviso" : "bfrOk");

    var acoes = document.getElementById("bfrAcoes");
    acoes.innerHTML = '<button class="pri" id="bfrReload">Recarregar o cockpit</button>' +
                      '<button class="sec" id="bfrDepois">Agora não</button>';
    document.getElementById("bfrReload").onclick = function () { location.reload(); };
    document.getElementById("bfrDepois").onclick = fechar;
  }

  function recusar(rel) {
    var linhas = ["Lote recusado. Nada foi gravado."];
    if (rel.motivo) linhas.push(rel.motivo);
    (rel.falhas || []).forEach(function (f) {
      linhas.push("• " + f.arquivo + " — " + f.erro);
    });
    (rel.erros_pacote || []).forEach(function (f) {
      linhas.push("• " + f.arquivo + " — " + f.erro);
    });
    status(linhas.join("\n"), "bfrErr");
    document.getElementById("bfrEnviar").disabled = false;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", montar);
  } else {
    montar();
  }
})();
