async function main() {
  const res = await fetch("data.json");
  const data = await res.json();

  const summaryEl = document.getElementById("summary");
  summaryEl.textContent =
    `키워드 ${data.summary.total_keywords}개 · 문서 ${data.summary.total_docs}개 · 분석 완료 ${data.summary.analyzed_keywords}개`;

  const listEl = document.getElementById("keyword-list");
  const detailEl = document.getElementById("detail");

  data.keywords.forEach((entry) => {
    const item = document.createElement("button");
    item.className = "keyword-item";

    const keywordSpan = document.createElement("span");
    keywordSpan.textContent = entry.keyword;
    item.appendChild(keywordSpan);

    const countSpan = document.createElement("span");
    countSpan.className = "count";
    countSpan.textContent = entry.total_count;
    item.appendChild(countSpan);

    item.addEventListener("click", () => showDetail(entry));
    listEl.appendChild(item);
  });

  function showDetail(entry) {
    detailEl.innerHTML = "";

    const heading = document.createElement("h2");
    heading.textContent = entry.keyword;
    detailEl.appendChild(heading);

    const countPara = document.createElement("p");
    countPara.textContent = `총 ${entry.total_count}개 수집`;
    detailEl.appendChild(countPara);

    const sourceList = document.createElement("ul");
    sourceList.className = "source-list";
    Object.entries(entry.by_source).forEach(([source, count]) => {
      const li = document.createElement("li");
      li.textContent = `${source}: ${count}`;
      sourceList.appendChild(li);
    });
    detailEl.appendChild(sourceList);

    if (entry.analysis) {
      const analysisPre = document.createElement("pre");
      analysisPre.className = "analysis";
      analysisPre.textContent = entry.analysis;
      detailEl.appendChild(analysisPre);
    } else {
      const noPara = document.createElement("p");
      noPara.className = "no-analysis";
      noPara.textContent = "아직 분석된 내용이 없습니다.";
      detailEl.appendChild(noPara);
    }
  }
}

main();
