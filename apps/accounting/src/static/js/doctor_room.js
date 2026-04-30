(function () {
  const endpoints = (window.DOCTOR_ROOM && window.DOCTOR_ROOM.endpoints) || {};

  const searchInput = document.getElementById('searchInput');
  const searchBtn = document.getElementById('searchBtn');
  const resultsBody = document.getElementById('resultsBody');
  const currentBox = document.getElementById('currentBox');
  const searchMsg = document.getElementById('searchMsg');

  function setSearchMsg(text) {
    if (!searchMsg) return;
    searchMsg.textContent = text || '';
  }

  function escapeHtml(str) {
    return String(str || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  async function apiGet(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'خطا در ارتباط با سرور');
    return data;
  }

  async function apiPost(url, form) {
    const res = await fetch(url, {
      method: 'POST',
      body: form,
      credentials: 'same-origin'
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'خطا در ارتباط با سرور');
    return data;
  }

  function renderResults(items) {
    if (!resultsBody) return;

    if (!items || items.length === 0) {
      resultsBody.innerHTML = '<tr><td colspan="6" class="muted center">نتیجه‌ای یافت نشد</td></tr>';
      return;
    }

    resultsBody.innerHTML = items
      .map((it) => {
        const invoiceId = it.invoice_id;
        const patientName = escapeHtml(it.patient_name);
        const nationalId = escapeHtml(it.national_id || '—');
        const openedAt = escapeHtml(it.opened_at || '');
        const openedBy = escapeHtml(it.opened_by_name || it.opened_by || '');

        return (
          '<tr>' +
          `<td>${invoiceId}</td>` +
          `<td>${patientName}</td>` +
          `<td>${nationalId}</td>` +
          `<td>${openedAt}</td>` +
          `<td>${openedBy}</td>` +
          `<td><button class="btn btn-primary" data-invoice="${invoiceId}">انتخاب</button></td>` +
          '</tr>'
        );
      })
      .join('');

    // bind buttons
    resultsBody.querySelectorAll('button[data-invoice]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const invoiceId = btn.getAttribute('data-invoice');
        if (!invoiceId) return;
        btn.disabled = true;
        try {
          const fd = new FormData();
          fd.append('invoice_id', invoiceId);
          await apiPost(endpoints.setRoom, fd);
          await loadCurrent();
        } catch (e) {
          alert(e.message || 'خطا');
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  function renderCurrent(payload) {
    if (!currentBox) return;

    if (!payload || !payload.current) {
      currentBox.innerHTML = (
        '<div class="muted">اتاق خالی است. از بالا فاکتور را جستجو و انتخاب کنید.</div>' +
        '<div class="actions-row">' +
        '<button id="clearBtn" class="btn btn-danger" type="button" disabled>خالی کردن اتاق</button>' +
        '</div>'
      );
      return;
    }

    const c = payload.current;
    const history = payload.patient_recent_visits || [];

    currentBox.innerHTML =
      '<div class="kv">' +
      `<div class="k">فاکتور</div><div class="v">${escapeHtml(c.invoice_id)}</div>` +
      `<div class="k">بیمار</div><div class="v">${escapeHtml(c.patient_name)}</div>` +
      `<div class="k">کد ملی</div><div class="v">${escapeHtml(c.national_id || '—')}</div>` +
      `<div class="k">شماره تماس</div><div class="v">${escapeHtml(c.phone_number || '—')}</div>` +
      `<div class="k">بیمه</div><div class="v">${escapeHtml(c.insurance_type || '—')}</div>` +
      `<div class="k">بیمه تکمیلی</div><div class="v">${escapeHtml(c.supplementary_insurance || '—')}</div>` +
      `<div class="k">باز شده توسط</div><div class="v">${escapeHtml(c.opened_by_name || c.opened_by || '')}</div>` +
      '</div>' +
      '<div class="actions-row">' +
      '<button id="clearBtn" class="btn btn-danger" type="button">خروج بیمار (خالی کردن اتاق)</button>' +
      '</div>' +
      '<div class="card-title" style="margin-top:14px;">آخرین ویزیت‌های بیمار</div>' +
      (history.length
        ? '<ul class="list">' +
          history
            .map((v) => {
              const vd = escapeHtml(v.visit_date || '');
              const doc = escapeHtml(v.doctor_name || '');
              const note = escapeHtml(v.notes || '');
              return `<li><div><strong>${vd}</strong> — ${doc}</div>${note ? `<div class="muted">${note}</div>` : ''}</li>`;
            })
            .join('') +
          '</ul>'
        : '<div class="muted">سابقه‌ای یافت نشد</div>');

    const clearBtn = document.getElementById('clearBtn');
    if (clearBtn) {
      clearBtn.addEventListener('click', async () => {
        clearBtn.disabled = true;
        try {
          await apiPost(endpoints.clearRoom, new FormData());
          await loadCurrent();
        } catch (e) {
          alert(e.message || 'خطا');
        } finally {
          clearBtn.disabled = false;
        }
      });
    }
  }

  async function loadCurrent() {
    if (!endpoints.current) return;
    if (currentBox) currentBox.textContent = 'در حال بارگذاری...';
    try {
      const data = await apiGet(endpoints.current);
      renderCurrent(data.data);
    } catch (e) {
      if (currentBox) currentBox.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message || 'خطا')}</div>`;
    }
  }

  async function runSearch() {
    const q = (searchInput && searchInput.value) ? searchInput.value.trim() : '';
    if (!q) {
      setSearchMsg('لطفاً نام بیمار یا کد ملی را وارد کنید.');
      renderResults([]);
      return;
    }

    setSearchMsg('در حال جستجو...');
    try {
      const url = new URL(endpoints.openInvoices, window.location.origin);
      url.searchParams.set('q', q);
      const data = await apiGet(url.toString());
      renderResults(data.items);
      setSearchMsg(data.items && data.items.length ? `${data.items.length} نتیجه` : 'نتیجه‌ای یافت نشد');
    } catch (e) {
      setSearchMsg('');
      alert(e.message || 'خطا');
    }
  }

  function wireEvents() {
    if (searchBtn) searchBtn.addEventListener('click', runSearch);
    if (searchInput) {
      searchInput.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') {
          ev.preventDefault();
          runSearch();
        }
      });
    }
  }

  wireEvents();
  loadCurrent();
})();
