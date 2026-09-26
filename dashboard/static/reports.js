const form = document.getElementById("report-form");

function query() {
  return new URLSearchParams(new FormData(form)).toString();
}

async function runReport() {
  const res = await fetch("/api/reports/daily?" + query());
  const rows = await res.json();
  const tbody = document.querySelector("#report-table tbody");
  tbody.innerHTML = "";
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    window.REPORT_COLUMNS.forEach((key) => {
      const td = document.createElement("td");
      td.textContent = r[key];
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  if (!rows.length) tbody.innerHTML = '<tr><td colspan="99">No data for this range</td></tr>';
}

form.addEventListener("submit", (e) => { e.preventDefault(); runReport(); });
form.room.addEventListener("change", () => {
  const opt = form.room.selectedOptions[0];
  form.open.value = opt.dataset.open;
  form.close.value = opt.dataset.close;
});
document.getElementById("export-csv").addEventListener("click", () => {
  location.href = "/api/reports/daily.csv?" + query();
});
runReport();
