const result = document.getElementById("result");
document.getElementById("create-asset").addEventListener("click", async () => {
  const payload = {name: document.getElementById("name").value.trim(), asset_code: document.getElementById("asset-code").value.trim(), tracking_name: document.getElementById("tracking-name").value.trim(), tracking_code: document.getElementById("tracking-code").value.trim()};
  result.hidden = false; result.className = "result-box"; result.textContent = "正在添加…";
  try { const response = await fetch("/api/assets/create", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}); const data = await response.json(); result.className = `result-box ${data.success ? "success" : "error"}`; result.textContent = data.message; if (data.success) { localStorage.setItem("portfolio.selectedAssetId", data.asset.id); setTimeout(() => location.href=`/asset/manage?asset_id=${encodeURIComponent(data.asset.id)}`, 400); } } catch (error) { result.className="result-box error"; result.textContent=`添加失败：${error}`; }
});
