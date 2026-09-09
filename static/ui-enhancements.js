(() => {
  const root = document.documentElement;
  const themeToggle = document.querySelector('#themeToggle');
  const systemDark = window.matchMedia?.('(prefers-color-scheme: dark)');
  const savedTheme = localStorage.getItem('mocklab-theme');

  function applyTheme(theme, persist = false) {
    root.dataset.theme = theme;
    if (persist) localStorage.setItem('mocklab-theme', theme);
    if (!themeToggle) return;
    const dark = theme === 'dark';
    themeToggle.setAttribute('aria-pressed', String(dark));
    themeToggle.querySelector('.theme-label').textContent = dark ? '浅色' : '深色';
    themeToggle.title = dark ? '切换到浅色模式' : '切换到深色模式';
  }

  applyTheme(savedTheme || (systemDark?.matches ? 'dark' : 'light'));
  themeToggle?.addEventListener('click', () => applyTheme(root.dataset.theme === 'dark' ? 'light' : 'dark', true));
  systemDark?.addEventListener?.('change', event => {
    if (!localStorage.getItem('mocklab-theme')) applyTheme(event.matches ? 'dark' : 'light');
  });

  const appDialog = document.querySelector('#appDialog');
  const appDialogTitle = document.querySelector('#appDialogTitle');
  const appDialogBody = document.querySelector('#appDialogBody');
  const appDialogField = document.querySelector('#appDialogField');
  const appDialogInputLabel = document.querySelector('#appDialogInputLabel');
  const appDialogInput = document.querySelector('#appDialogInput');
  const appDialogCancel = document.querySelector('#appDialogCancel');
  const appDialogConfirm = document.querySelector('#appDialogConfirm');
  let appDialogResolve = null;
  let appDialogLastFocus = null;

  function closeAppDialog(value) {
    if (!appDialog || appDialog.hidden) return;
    appDialog.hidden = true;
    const resolve = appDialogResolve;
    appDialogResolve = null;
    resolve?.(value);
    appDialogLastFocus?.focus();
  }

  window.openAppDialog = ({title, body, confirmLabel = '确认', cancelLabel = '取消', inputLabel = '', inputValue = ''}) => new Promise(resolve => {
    if (!appDialog) { resolve(false); return; }
    if (appDialogResolve) closeAppDialog(false);
    appDialogResolve = resolve;
    appDialogLastFocus = document.activeElement;
    appDialogTitle.textContent = title;
    appDialogBody.textContent = body;
    appDialogConfirm.textContent = confirmLabel;
    appDialogCancel.textContent = cancelLabel || '取消';
    appDialogCancel.hidden = !cancelLabel;
    appDialogField.hidden = !inputLabel;
    appDialogInputLabel.textContent = inputLabel || '';
    appDialogInput.value = inputValue;
    appDialog.hidden = false;
    requestAnimationFrame(() => inputLabel ? appDialogInput.focus() : appDialogConfirm.focus());
  });

  appDialogConfirm?.addEventListener('click', () => closeAppDialog(appDialogField.hidden ? true : appDialogInput.value.trim() || null));
  appDialogCancel?.addEventListener('click', () => closeAppDialog(appDialogField.hidden ? false : null));
  appDialog?.querySelector('[data-app-dialog-close]')?.addEventListener('click', () => closeAppDialog(appDialogField.hidden ? false : null));
  appDialogInput?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) closeAppDialog(appDialogInput.value.trim() || null);
  });

  document.querySelector('.brand')?.addEventListener('click', event => {
    event.preventDefault();
    show('welcome');
  });

  function activateTargetView(view, focus = false) {
    const target = view === 'target';
    const targetPanel = document.querySelector('#targetJobPanel');
    const generalPanel = document.querySelector('#generalJobPanel');
    const confirmButton = document.querySelector('#confirmBtn');
    const generalButton = document.querySelector('#skipJob');
    document.querySelectorAll('[data-target-view]').forEach(button => {
      const selected = button.dataset.targetView === view;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    });
    targetPanel.hidden = !target;
    generalPanel.hidden = target;
    confirmButton.hidden = !target;
    generalButton.hidden = target;
  }

  const targetTabs = [...document.querySelectorAll('[data-target-view]')];
  targetTabs.forEach((button, index) => {
    button.addEventListener('click', () => activateTargetView(button.dataset.targetView));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === 'ArrowLeft') next = (index - 1 + targetTabs.length) % targetTabs.length;
      if (event.key === 'ArrowRight') next = (index + 1) % targetTabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = targetTabs.length - 1;
      activateTargetView(targetTabs[next].dataset.targetView, true);
    });
  });

  const dropzone = document.querySelector('#dropzone');
  const resumeFile = document.querySelector('#resumeFile');
  const resumeFileName = document.querySelector('#resumeFileName');
  ['dragenter', 'dragover'].forEach(name => dropzone?.addEventListener(name, event => {
    event.preventDefault();
    dropzone.classList.add('is-dragging');
  }));
  ['dragleave', 'drop'].forEach(name => dropzone?.addEventListener(name, () => dropzone.classList.remove('is-dragging')));
  resumeFile?.addEventListener('change', () => {
    const file = resumeFile.files?.[0];
    dropzone.classList.toggle('has-file', !!file);
    resumeFileName.hidden = !file;
    resumeFileName.textContent = file ? `已选择：${file.name}` : '';
  });

  function bindTabList(host, panelSelector) {
    const tabs = [...host.querySelectorAll('[role="tab"]')];
    const panels = [...document.querySelectorAll(panelSelector)];
    function select(tab, focus = false) {
      tabs.forEach(item => {
        const active = item === tab;
        item.setAttribute('aria-selected', String(active));
        item.tabIndex = active ? 0 : -1;
      });
      panels.forEach(panel => panel.hidden = panel.id !== tab.getAttribute('aria-controls'));
      if (focus) tab.focus();
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => select(tab));
      tab.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : event.key === 'ArrowLeft' ? (index - 1 + tabs.length) % tabs.length : (index + 1) % tabs.length;
        select(tabs[next], true);
      });
    });
  }

  function clampScore(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, Math.min(5, number)) : null;
  }

  function scoreLabel(value) {
    const score = clampScore(value);
    return score === null ? '未考察' : `${score.toFixed(1)} / 5`;
  }

  function unique(items) {
    return [...new Set(items.filter(Boolean))];
  }

  renderReport = function renderEnhancedReport(report) {
    const host = document.querySelector('#reportContent');
    document.querySelector('#postTestBtn').hidden = report.phase === 'post_test' || !report.complete;
    if (report.error) {
      host.innerHTML = `<div class="empty-state"><div class="empty-state-inner"><div class="empty-state-icon">!</div><h3>报告暂不可用</h3><p>${esc(report.error)}</p></div></div>`;
      return;
    }

    const topics = Array.isArray(report.topics) ? report.topics : [];
    const score = clampScore(report.overall_score);
    const scorePercent = score === null ? 0 : Math.round(score / 5 * 100);
    const covered = Math.round((Number(report.coverage) || 0) * 100);
    const assessed = topics.filter(topic => clampScore(topic.score) !== null);
    const strongest = [...assessed].sort((a, b) => Number(b.score) - Number(a.score))[0];
    const confidenceValues = assessed
      .filter(topic => topic.confidence !== null && topic.confidence !== undefined && topic.confidence !== '')
      .map(topic => Number(topic.confidence))
      .filter(Number.isFinite);
    const confidence = confidenceValues.length ? Math.round(confidenceValues.reduce((sum, value) => sum + value, 0) / confidenceValues.length * 100) : null;
    const title = report.phase === 'post_test' ? '平行后测报告' : report.complete ? '完整面试报告' : '不完整面试报告';
    const strengths = unique(topics.flatMap(topic => topic.strengths || []));
    const gaps = unique(topics.flatMap(topic => topic.gaps || []));

    const competencyCards = topics.map((topic, index) => {
      const topicScore = clampScore(topic.score);
      const width = topicScore === null ? 0 : Math.round(topicScore / 5 * 100);
      const detailId = `competency-detail-${index}`;
      const strengthsHtml = (topic.strengths || []).map(item => `<li>${esc(item)}</li>`).join('');
      const gapsHtml = (topic.gaps || []).map(item => `<li>${esc(item)}</li>`).join('');
      const scoreVisual = topicScore === null
        ? '<div class="score-unassessed">本主题没有形成有效评分</div>'
        : `<div class="score-bar" aria-label="${esc(topic.name)}得分 ${scoreLabel(topic.score)}"><i style="width:${width}%"></i></div>`;
      return `<article class="competency-card"><div class="competency-head"><div><b>${esc(topic.name)}</b><small>${esc(topic.status || '')} · 置信度 ${topic.confidence == null ? '—' : `${Math.round(Number(topic.confidence) * 100)}%`}</small></div><span class="competency-score">${scoreLabel(topic.score)}</span></div>${scoreVisual}<button class="report-expand" type="button" aria-expanded="false" aria-controls="${detailId}"><span>查看评价依据</span><span aria-hidden="true">⌄</span></button><div id="${detailId}" class="report-details" hidden><b>评价边界</b><p>${esc(topic.evaluation_basis || '依据回答的结构、相关性和证据进行训练评价。')}</p>${strengthsHtml ? `<b>做得好的地方</b><ul>${strengthsHtml}</ul>` : ''}${gapsHtml ? `<b>需要补充</b><ul>${gapsHtml}</ul>` : ''}<blockquote>${esc(topic.evidence || '暂无可引用证据')}</blockquote></div></article>`;
    }).join('') || '<div class="empty-state"><div class="empty-state-inner"><h3>暂无能力评价</h3><p>完成更多有效回答后，这里会出现分项分析。</p></div></div>';

    const timeline = topics.map((topic, index) => {
      const questions = (topic.actual_questions?.length ? topic.actual_questions : topic.question ? [{question: topic.question, kind: 'main'}] : []);
      const questionHtml = questions.map(item => `<p><b>${item.kind === 'generated_followup' ? '追问' : '主问题'}：</b>${esc(item.question)}</p>`).join('');
      const sources = (topic.sources || []).map(source => `<li>${esc(source.source || '题库')} · ${esc(source.company || '通用')} ${esc(source.role || '')}</li>`).join('');
      return `<article class="timeline-entry"><div class="timeline-meta"><span>第 ${index + 1} 个能力主题</span><span>${scoreLabel(topic.score)}</span></div><h3>${esc(topic.name)}</h3>${questionHtml || '<p>本场未覆盖该主题。</p>'}${sources ? `<button class="report-expand" type="button" aria-expanded="false" aria-controls="source-detail-${index}"><span>查看题目来源</span><span aria-hidden="true">⌄</span></button><div id="source-detail-${index}" class="report-details" hidden><ul>${sources}</ul></div>` : ''}</article>`;
    }).join('') || '<div class="empty-state"><div class="empty-state-inner"><h3>暂无问答记录</h3><p>完成面试后可在这里按时间顺序回看问题。</p></div></div>';

    const improvementItems = topics.map((topic, index) => `<article class="improvement-card"><span>${String(index + 1).padStart(2, '0')}</span><div><h3>${esc(topic.name)}</h3><p>${esc(topic.suggestion || '使用结论—依据—边界结构重新组织回答。')}</p></div></article>`).join('');

    host.innerHTML = `<div class="report-hero"><div><div class="step">${report.complete ? '训练已完成' : '训练提前结束'}</div><h2>${title}</h2><p class="muted">${esc(report.score_note || '')}</p></div><div class="report-score-ring" style="--score:${scorePercent}" aria-label="综合得分 ${scoreLabel(score)}"><div class="score">${score === null ? '—' : score.toFixed(1)}<small>/ 5</small></div></div></div>
      <div class="report-bento"><article class="report-metric primary-metric"><span>本场结论</span><b>${score === null ? '待补充回答' : score >= 4 ? '表现稳定' : score >= 3 ? '具备基础' : '优先补强'}</b></article><article class="report-metric"><span>主题覆盖</span><b>${covered}%</b></article><article class="report-metric"><span>有效回答</span><b>${Number(report.turn_count) || 0}</b></article><article class="report-metric"><span>${strongest ? '优势主题' : '评测模式'}</span><b>${esc(strongest?.name || (report.mode === 'validated' ? '正式' : '实验'))}</b></article></div>
      <div class="report-tabs" role="tablist" aria-label="报告内容"><button id="reportOverviewTab" role="tab" aria-selected="true" aria-controls="reportOverview" tabindex="0">能力总览</button><button id="reportAnswersTab" role="tab" aria-selected="false" aria-controls="reportAnswers" tabindex="-1">逐题回放</button><button id="reportImproveTab" role="tab" aria-selected="false" aria-controls="reportImprove" tabindex="-1">改进计划</button></div>
      <section id="reportOverview" class="report-panel" role="tabpanel" aria-labelledby="reportOverviewTab"><div class="competency-grid">${competencyCards}</div></section>
      <section id="reportAnswers" class="report-panel" role="tabpanel" aria-labelledby="reportAnswersTab" hidden><div class="answer-timeline">${timeline}</div></section>
      <section id="reportImprove" class="report-panel" role="tabpanel" aria-labelledby="reportImproveTab" hidden><div class="report-bento"><article class="report-metric"><span>已识别优势</span><b>${strengths.length}</b></article><article class="report-metric"><span>待改善项</span><b>${gaps.length}</b></article><article class="report-metric"><span>平均置信度</span><b>${confidence == null ? '—' : `${confidence}%`}</b></article><article class="report-metric"><span>建议节奏</span><b>逐项重答</b></article></div><div class="improvement-list">${improvementItems || '<p class="muted">完成面试后生成改进计划。</p>'}</div></section>`;

    bindTabList(host.querySelector('.report-tabs'), '.report-panel');
    host.querySelectorAll('.report-expand').forEach(button => button.addEventListener('click', () => {
      const panel = document.getElementById(button.getAttribute('aria-controls'));
      const expanded = button.getAttribute('aria-expanded') === 'true';
      button.setAttribute('aria-expanded', String(!expanded));
      panel.hidden = expanded;
    }));
  };

  async function renderHistoryDashboard() {
    const summary = document.querySelector('#historySummary');
    const list = document.querySelector('#historyList');
    summary.innerHTML = '<article class="dashboard-metric primary-metric"><span>正在读取</span><b>练习记录…</b></article>';
    list.innerHTML = '';
    try {
      const rows = await api('/api/sessions');
      const completedRows = rows.filter(row => row.has_report && row.status === 'FINISHED');
      const reportRows = completedRows.slice(0, 16);
      const reports = await Promise.all(reportRows.map(row => api(`/api/sessions/${row.id}/report`).catch(() => null)));
      const scores = reports
        .filter(report => report?.overall_score !== null && report?.overall_score !== undefined && report?.overall_score !== '')
        .map(report => Number(report.overall_score))
        .filter(Number.isFinite);
      const average = scores.length ? (scores.reduce((sum, value) => sum + value, 0) / scores.length).toFixed(1) : '—';
      const finished = completedRows.length;
      const latest = rows[0]?.updated_at ? new Date(rows[0].updated_at).toLocaleDateString('zh-CN', {month: 'short', day: 'numeric'}) : '暂无';
      summary.innerHTML = `<article class="dashboard-metric primary-metric"><span>累计练习</span><b>${rows.length} 场</b></article><article class="dashboard-metric"><span>已生成报告</span><b>${finished}</b></article><article class="dashboard-metric"><span>平均得分</span><b>${average}${average === '—' ? '' : ' / 5'}</b></article><article class="dashboard-metric"><span>最近练习</span><b>${esc(latest)}</b></article>`;
      let visibleCount = 10;
      const renderRows = () => {
        if (!rows.length) {
          list.innerHTML = '<div class="empty-state"><div class="empty-state-inner"><div class="empty-state-icon">＋</div><h3>还没有练习记录</h3><p>从一场 20-30 分钟的模拟面试开始。完成后，这里会汇总报告与训练节奏。</p><button id="emptyStart" class="primary" type="button">创建第一场面试</button></div></div>';
          document.querySelector('#emptyStart')?.addEventListener('click', () => show('welcome'));
          return;
        }
        const visibleRows = rows.slice(0, visibleCount);
        const moreButton = visibleCount < rows.length
          ? `<button id="showMoreHistory" class="secondary history-more" type="button">再显示 ${Math.min(10, rows.length - visibleCount)} 条</button>`
          : '';
        list.innerHTML = visibleRows.map((row, index) => `<article class="history-row"><div class="history-row-main"><div class="history-icon">${String(index + 1).padStart(2, '0')}</div><div><b>${esc(row.job_title || '未完成的练习')}</b><br><small>${new Date(row.updated_at).toLocaleString()} · ${row.mode === 'validated' ? '正式评测' : '实验 / 草稿'}</small></div></div><div class="history-actions">${row.has_report && row.status === 'FINISHED' ? `<button class="small" type="button" data-open-report="${row.id}">查看报告</button>` : `<span class="status-chip">${row.status === 'ASKING' ? '进行中' : '未完成'}</span>`}<button class="small danger" type="button" data-remove-record="${row.id}">删除</button></div></article>`).join('') + moreButton;
        list.querySelectorAll('[data-open-report]').forEach(button => button.addEventListener('click', () => window.openReport(button.dataset.openReport)));
        list.querySelectorAll('[data-remove-record]').forEach(button => button.addEventListener('click', () => window.removeRecord(button.dataset.removeRecord)));
        document.querySelector('#showMoreHistory')?.addEventListener('click', () => { visibleCount += 10; renderRows(); });
      };
      renderRows();
      show('history');
    } catch (error) {
      summary.innerHTML = '';
      list.innerHTML = `<div class="empty-state"><div class="empty-state-inner"><div class="empty-state-icon">!</div><h3>暂时无法读取记录</h3><p>${esc(error.message)}</p><button id="retryHistory" class="secondary" type="button">重新加载</button></div></div>`;
      document.querySelector('#retryHistory')?.addEventListener('click', renderHistoryDashboard);
      show('history');
    }
  }

  document.querySelector('#historyBtn').onclick = renderHistoryDashboard;
  document.querySelector('#dashboardStart')?.addEventListener('click', () => show('welcome'));
  document.querySelector('#newFromDashboard')?.addEventListener('click', () => show('welcome'));
  document.querySelector('#banksFromDashboard')?.addEventListener('click', () => document.querySelector('#welcomeBanks').click());

  document.addEventListener('keydown', event => {
    const modal = document.querySelector('.modal-shell:not([hidden])');
    if (!modal) return;
    if (event.key === 'Escape' && modal === appDialog) { event.preventDefault(); closeAppDialog(appDialogField.hidden ? false : null); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...modal.querySelectorAll('button:not([disabled]):not([hidden]), input:not([disabled]):not([hidden]), textarea:not([disabled]):not([hidden]), select:not([disabled]):not([hidden]), [tabindex]:not([tabindex="-1"])')];
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
})();
