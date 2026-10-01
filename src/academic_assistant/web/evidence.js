"use strict";

(() => {
  const idPattern = /^[a-z0-9][a-z0-9._-]{2,127}$/;
  const genericError = "근거 페이지를 표시할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  let dialog;
  let body;
  let closeButton;
  let sequence = 0;
  let controller = null;
  let returnFocus = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function invalidate() {
    sequence += 1;
    if (controller) controller.abort();
    controller = null;
  }

  function restoreFocus() {
    const target = returnFocus;
    returnFocus = null;
    if (target && target.isConnected) target.focus();
  }

  function close() {
    invalidate();
    if (dialog && dialog.open) dialog.close();
  }

  function ensureDialog() {
    if (dialog) return;
    dialog = element("dialog", "academic-evidence-dialog");
    dialog.setAttribute("aria-labelledby", "academic-evidence-heading");
    dialog.setAttribute("aria-describedby", "academic-evidence-purpose");
    const header = element("div", "evidence-dialog-header");
    const titles = element("div");
    const title = element("h2", "evidence-dialog-title", "근거 PDF 보기");
    title.id = "academic-evidence-heading";
    const purpose = element("p", "evidence-dialog-purpose", "승인된 규칙의 원문 위치를 확인합니다.");
    purpose.id = "academic-evidence-purpose";
    titles.append(title, purpose);
    closeButton = element("button", "evidence-close", "닫기");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "근거 PDF 닫기");
    closeButton.addEventListener("click", close);
    header.append(titles, closeButton);
    body = element("div", "evidence-dialog-body");
    dialog.append(header, body);
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close();
    });
    dialog.addEventListener("close", () => {
      // Native close events are queued; a reopened dialog owns newer state.
      if (dialog.open) return;
      invalidate();
      body.replaceChildren();
      restoreFocus();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
    document.body.appendChild(dialog);
  }

  function assetUrl(value, ruleId, extension, index, page) {
    const expectedPath = `/v1/academic/evidence/${ruleId}/preview.${extension}`;
    if (typeof value !== "string" || !value.startsWith(`${expectedPath}?`) || /[\s\\]/.test(value)) {
      throw new Error("invalid preview URL");
    }
    const url = new URL(value, window.location.href);
    const keys = Array.from(url.searchParams.keys());
    if (url.origin !== window.location.origin || url.pathname !== expectedPath || url.hash
        || url.username || url.password || keys.length !== 2
        || url.searchParams.getAll("evidence_index").length !== 1
        || url.searchParams.getAll("pdf_page").length !== 1
        || url.searchParams.get("evidence_index") !== String(index)
        || url.searchParams.get("pdf_page") !== String(page)) {
      throw new Error("invalid preview URL");
    }
    return url.pathname + url.search;
  }

  function validate(data, ref, index) {
    if (!data || data.schema_version !== "1.0.0" || data.rule_id !== ref.rule_id
        || typeof data.source_id !== "string" || !idPattern.test(data.source_id)
        || (typeof ref.source_id === "string" && data.source_id !== ref.source_id)
        || typeof data.source_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(data.source_sha256)
        || !Number.isSafeInteger(data.pdf_page) || data.pdf_page < 1
        || !(data.printed_page === null || (Number.isSafeInteger(data.printed_page) && data.printed_page >= 1))
        || !["exact", "page_only"].includes(data.precision)
        || ![data.quote, data.location, data.notice].every((value) => typeof value === "string" && value.length > 0)) {
      throw new Error("invalid preview metadata");
    }
    return {
      ...data,
      image_url: assetUrl(data.image_url, ref.rule_id, "png", index, data.pdf_page),
      pdf_url: assetUrl(data.pdf_url, ref.rule_id, "pdf", index, data.pdf_page),
    };
  }

  function status(text, error = false) {
    const message = element("p", error ? "evidence-message evidence-error" : "evidence-message", text);
    message.setAttribute("role", error ? "alert" : "status");
    message.setAttribute("aria-live", "polite");
    body.replaceChildren(message);
  }

  function render(data, token) {
    const layout = element("div", "evidence-layout");
    const details = element("section", "evidence-details");
    details.setAttribute("aria-label", "근거 정보");
    const exact = data.precision === "exact";
    const precision = element("p", `evidence-precision ${exact ? "evidence-exact" : "evidence-page-only"}`,
      exact ? "인용 위치 확인" : "페이지 근거 · 위치 미확인");
    const source = element("p", "evidence-source", data.source_id);
    const pageText = data.printed_page === null
      ? `PDF ${data.pdf_page}페이지 · 인쇄 페이지 표기 없음`
      : `PDF ${data.pdf_page}페이지 · 인쇄 ${data.printed_page}페이지`;
    const pages = element("p", "evidence-pages", pageText);
    const quoteTitle = element("h3", "evidence-quote-heading", "승인된 인용");
    const quote = element("blockquote", "evidence-quote", data.quote);
    const location = element("p", "evidence-location", data.location);
    const notice = element("p", "evidence-notice", data.notice);
    const boundary = element("p", "evidence-boundary", "표시된 근거는 규칙 확인용입니다. 개인의 졸업 가능 여부를 판정하지 않습니다.");
    const download = element("a", "evidence-download", "표시된 페이지 PDF 내려받기 (복사본)");
    download.href = data.pdf_url;
    download.download = `citation-${data.rule_id}-p${data.pdf_page}.pdf`;
    const copyNote = element("p", "evidence-copy-note", "현재 페이지의 표시 내용을 담은 한 페이지 PDF 복사본입니다.");
    details.append(precision, source, pages, quoteTitle, quote, location, notice, boundary, download, copyNote);

    const figure = element("figure", "evidence-figure");
    const caption = element("figcaption", "evidence-image-status", "페이지 이미지를 불러오는 중입니다.");
    caption.setAttribute("role", "status");
    const image = element("img", "evidence-preview-image");
    image.alt = `근거 PDF ${data.pdf_page}페이지: ${data.quote}`;
    image.addEventListener("load", () => {
      if (token !== sequence || !dialog.open || !image.isConnected) return;
      caption.textContent = exact ? "빨간 밑줄은 검증된 인용 위치를 나타냅니다." : "정확한 위치가 확인되지 않아 밑줄 없이 페이지를 표시합니다.";
    });
    image.addEventListener("error", () => {
      if (token !== sequence || !dialog.open || !image.isConnected) return;
      image.hidden = true;
      caption.className = "evidence-image-status evidence-error";
      caption.textContent = genericError;
    });
    figure.append(caption, image);
    layout.append(details, figure);
    body.replaceChildren(layout);
    image.src = data.image_url;
  }

  async function open(ref, index, trigger) {
    ensureDialog();
    invalidate();
    const token = sequence;
    returnFocus = trigger;
    status("승인된 근거 페이지를 불러오는 중입니다.");
    if (!dialog.open) dialog.showModal();
    closeButton.focus();
    controller = new AbortController();
    const activeController = controller;
    let responseStatus;
    try {
      const response = await fetch(`/v1/academic/evidence/${ref.rule_id}/preview?evidence_index=${index}`, {
        signal: activeController.signal,
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (token !== sequence || !dialog.open) return;
      responseStatus = response.status;
      if (!response.ok) throw new Error("preview unavailable");
      const data = await response.json();
      if (token !== sequence || !dialog.open) return;
      render(validate(data, ref, index), token);
    } catch (_error) {
      if (token !== sequence || !dialog.open) return;
      status(responseStatus === 429
        ? "근거 페이지를 확인 중인 요청이 있습니다. 잠시 후 다시 시도해 주세요."
        : genericError, true);
    }
  }

  function button(ref, evidenceIndex = 0) {
    if (ref && ref.source_id === "cwnu.cs.2026.department-confirmation-20261001") {
      // A human confirmation is not text in the original curriculum PDF.
      // Never issue a PDF preview request or fabricate an underline for it.
      const notice = element("span", "evidence-notice", "학과 확인 기록 · PDF 원문 아님");
      notice.setAttribute("aria-label", "학과 확인 기록이며 PDF 원문 인용이 아닙니다.");
      return notice;
    }
    const trigger = element("button", "evidence-open", "근거 PDF 보기");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "dialog");
    const valid = ref && typeof ref.rule_id === "string" && idPattern.test(ref.rule_id)
      && Number.isSafeInteger(evidenceIndex) && evidenceIndex >= 0;
    if (!valid) {
      trigger.disabled = true;
      trigger.title = "확인 가능한 근거 식별자가 없습니다.";
      return trigger;
    }
    // Capture only citation fields; callers may rerender or mutate their data.
    const citation = { rule_id: ref.rule_id, source_id: ref.source_id };
    trigger.addEventListener("click", () => open(citation, evidenceIndex, trigger));
    return trigger;
  }

  window.AcademicEvidence = Object.freeze({ button });
})();
