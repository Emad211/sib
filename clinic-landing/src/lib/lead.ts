import { CONFIG } from "../config";

const toEn = (s: string) =>
  s.replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d))).replace(/[٠-٩]/g, (d) => String("٠١٢٣٤٥٦٧٨٩".indexOf(d)));

/** شمارهٔ موبایل ایران را به شکل 09xxxxxxxxx درمی‌آورد؛ اعداد فارسی، فاصله و ‎+98 را می‌پذیرد */
export function normalizeMobile(v: string): string | null {
  let s = toEn(v).replace(/[\s\-()+]/g, "");
  if (s.startsWith("0098")) s = "0" + s.slice(4);
  else if (s.startsWith("98")) s = "0" + s.slice(2);
  else if (s.startsWith("9") && s.length === 10) s = "0" + s;
  return /^09\d{9}$/.test(s) ? s : null;
}

export function bindLeadForms() {
  const forms = document.querySelectorAll<HTMLFormElement>(".js-lead");
  forms.forEach((form) => {
    const input = form.querySelector<HTMLInputElement>("input[name=mobile]")!;
    const msg = form.querySelector<HTMLElement>(".lead-msg")!;
    const btn = form.querySelector<HTMLButtonElement>("button[type=submit]")!;
    input.addEventListener("input", () => {
      msg.textContent = "";
      msg.classList.remove("err");
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const mobile = normalizeMobile(input.value);
      if (!mobile) {
        msg.textContent = "لطفاً شمارهٔ موبایل را درست وارد کنید؛ مثل ۰۹۱۲۱۲۳۴۵۶۷";
        msg.classList.add("err");
        input.focus();
        return;
      }
      const lead = {
        mobile,
        condition: form.querySelector<HTMLInputElement>("input[name=cond]:checked")?.value ?? "",
        source: location.href,
        at: new Date().toISOString(),
      };
      btn.disabled = true;
      try {
        if (CONFIG.leadEndpoint) {
          const r = await fetch(CONFIG.leadEndpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(lead) });
          if (!r.ok) throw new Error(String(r.status));
        } else {
          try {
            const all = JSON.parse(localStorage.getItem("leads") || "[]");
            all.push(lead);
            localStorage.setItem("leads", JSON.stringify(all));
          } catch {
            /* ذخیره‌سازی مرورگر در دسترس نیست */
          }
        }
        forms.forEach((f) => f.classList.add("done"));
      } catch {
        msg.textContent = `ارسال انجام نشد. لطفاً دوباره تلاش کنید یا با ${CONFIG.phoneDisplay} تماس بگیرید.`;
        msg.classList.add("err");
      } finally {
        btn.disabled = false;
      }
    });
  });
}
