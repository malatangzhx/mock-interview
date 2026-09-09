(() => {
  const steps = [
    ['aiSetup', 'AI 接入'], ['profileSetup', '个人资料'], ['jobSetup', '目标岗位'],
    ['bankSetup', '本场题库'], ['planSetup', '面试计划'], ['ready', '面试调试'], ['interview', '正式面试']
  ];
  const flow = {current: 0, completed: new Set(), aiMode: null, profileReady: false, jobReady: false, recommendationsLoaded: false};
  const checks = {camera: 'idle', mic: 'idle', speech: 'idle', model: 'idle'};
  let cameraBypassed = false, speechFallback = false, lastFocus = null;
  const labels = {idle: '未检测', checking: '检测中', pass: '通过', attention: '需要处理', skipped: '已跳过'};
  const baseShow = show;
  const oldStartHandler = $('#startBtn').onclick;

  function renderFlowIndex() {
    $$('.flow-index').forEach(host => {
      const current = Number(host.closest('[data-flow-step]')?.dataset.flowStep || 0);
      host.innerHTML = `<nav class="flow-track" aria-label="面试准备步骤">${steps.map(([id, name], i) => {
        const done = flow.completed.has(i), active = i === current, enabled = done || active;
        return `<button class="flow-step ${active ? 'current' : done ? 'done' : ''}" data-step-target="${id}" ${enabled ? '' : 'disabled'} aria-current="${active ? 'step' : 'false'}"><i>${done && !active ? '✓' : i + 1}</i>${esc(name)}</button>`;
      }).join('')}</nav>`;
    });
    $$('[data-step-target]').forEach(button => button.onclick = () => show(button.dataset.stepTarget));
  }

  show = id => {
    baseShow(id);
    const index = steps.findIndex(([stepId]) => stepId === id);
    if (index >= 0) flow.current = index;
    renderFlowIndex();
    if (id === 'bankSetup') loadRecommendations();
    if (id === 'ready') prepareDebugPage();
  };

  function complete(index) {
    flow.completed.add(index);
    renderFlowIndex();
  }

  function invalidateFrom(index) {
    for (let i = index; i < steps.length; i++) flow.completed.delete(i);
    renderFlowIndex();
  }

  function setButton(button, stateName, label) {
    button.dataset.state = stateName;
    button.disabled = stateName === 'loading';
    button.textContent = label;
  }

  function showLoader(id, items, active = 0) {
    const loader = $(id);
    loader.hidden = false;
    loader.textContent = items.map((item, i) => `${i < active ? '✓' : i === active ? '●' : '○'} ${item}`).join('　');
  }

  function hideLoader(id) { $(id).hidden = true; }

  function applyConnection(result, mode) {
    flow.aiMode = mode;
    state.aiConnected = true;
    state.realtimeReady = !!result.realtime_supported;
    state.voiceMode = result.voice_mode || 'text';
    state.voiceProvider = result.voice_provider || result.provider;
    state.voiceLabel = result.voice_label || '';
    $('#aiEnabled').checked = true;
    $('#aiFields').hidden = true;
    $('#aiConnectedSummary').hidden = false;
    $('#aiConnectedModel').textContent = `${mode === 'system' ? '系统 AI' : '个人 AI'} · ${result.model}`;
    $('#aiConnectedLatency').textContent = `连接正常 · ${result.latency_ms ?? '—'} ms`;
    $('#aiStatus').classList.remove('error');
    $('#aiStatus').textContent = '连接已通过；之后的资料分析仅在你点击“开始分析”时触发。';
    $('#aiNextBtn').disabled = false;
    setModelStatus('connected', `已连接 · ${result.model}`);
    invalidateModelCheck();
  }

  async function chooseLocalMode() {
    if (state.session) await api(`/api/sessions/${state.session.id}/ai`, {method: 'DELETE'}).catch(() => {});
    flow.aiMode = 'local'; state.aiConnected = false; state.realtimeReady = false; state.voiceMode = 'text';
    $('#aiEnabled').checked = false; $('#aiFields').hidden = true; $('#aiConnectedSummary').hidden = false;
    $('#aiConnectedModel').textContent = '本地模式'; $('#aiConnectedLatency').textContent = '不使用 AI 追问与 AI 报告';
    $('#aiStatus').classList.remove('error'); $('#aiStatus').textContent = '可继续使用题库、确定性选题和本地报告。';
    $('#aiNextBtn').disabled = false; setModelStatus('offline', '本地模式 · 不使用模型'); invalidateModelCheck();
  }

  function invalidateModelCheck() {
    checks.model = 'idle';
    updateCheck('model', 'idle', flow.aiMode === 'local' ? '本地模式将在调试时明确跳过模型检测。' : 'AI 配置已变化，需要重新检测。');
  }

  $('#privacyCheck').onchange = event => $('#beginBtn').disabled = !event.target.checked;
  $('#beginBtn').onclick = async () => {
    try {
      state.session = await api('/api/sessions', {method: 'POST'});
      await Promise.all([loadCompanies(), loadUserBank()]);
      show('aiSetup');
    } catch (error) { toast(error.message); }
  };
  $$('[data-flow-back]').forEach(button => button.onclick = () => show(button.dataset.flowBack));

  $('#aiEnabled').onchange = event => {
    $('#aiFields').hidden = !event.target.checked;
    $('#aiConnectedSummary').hidden = true;
    $('#aiNextBtn').disabled = true;
    if (event.target.checked) { flow.aiMode = null; $('#aiStatus').textContent = '填写 API Key 后主动测试连接。'; }
    else chooseLocalMode();
  };
  $('#editAiConnection').onclick = () => { $('#aiFields').hidden = false; $('#aiConnectedSummary').hidden = true; $('#aiNextBtn').disabled = true; };
  $('#testAiBtn').onclick = async () => {
    const button = $('#testAiBtn');
    const payload = {provider: $('#aiProvider').value, base_url: $('#aiBaseUrl').value.trim(), realtime_url: $('#aiRealtimeUrl').value.trim() || null, api_key: $('#aiApiKey').value.trim(), model: $('#aiModel').value.trim(), wire_api: $('#aiWireApi').value, timeout_seconds: Number($('#aiTimeout').value), stream: $('#aiStream').checked};
    if (!payload.api_key || !payload.base_url || !payload.model) { $('#aiStatus').classList.add('error'); $('#aiStatus').textContent = '请填写服务商、模型和 API Key；接口地址可在高级设置中核对。'; return; }
    setButton(button, 'loading', '正在连接…'); $('#aiStatus').classList.remove('error'); $('#aiStatus').textContent = `正在验证 ${payload.model}，不会发送个人资料。`;
    try { const result = await api(`/api/sessions/${state.session.id}/ai/test`, json('POST', payload)); applyConnection(result, 'personal'); setButton(button, 'success', '连接成功'); }
    catch (error) { flow.aiMode = null; state.aiConnected = false; $('#aiStatus').classList.add('error'); $('#aiStatus').textContent = `连接失败：${error.message}`; setModelStatus('fallback', 'AI 连接需要处理'); setButton(button, 'error', '重试连接'); }
  };
  $('#useSystemAi').onclick = async () => {
    const button = $('#useSystemAi'); setButton(button, 'loading', '正在连接系统 AI…');
    try { const result = await api(`/api/sessions/${state.session.id}/ai/system`, {method: 'POST'}); applyConnection(result, 'system'); setButton(button, 'success', '系统 AI 已连接'); }
    catch (error) { $('#aiStatus').classList.add('error'); $('#aiStatus').textContent = error.message; setButton(button, 'error', '系统 AI 不可用'); }
  };
  $('#useLocalMode').onclick = chooseLocalMode;
  $('#aiNextBtn').onclick = () => { if (!flow.aiMode) return; complete(0); show('profileSetup'); };

  $('#resumeFile').onchange = async event => {
    const file = event.target.files[0]; if (!file) return;
    const form = new FormData(); form.append('file', file); $('#uploadStatus').textContent = '正在安全提取文本…';
    try { const result = await api(`/api/sessions/${state.session.id}/resume`, {method: 'POST', body: form}); $('#resumeText').value = result.text; $('#uploadStatus').textContent = `已提取 ${result.text.length} 字；原文件已删除。点击“分析简历”后才会调用 AI。`; flow.profileReady = false; }
    catch (error) { $('#uploadStatus').classList.add('error'); $('#uploadStatus').textContent = error.message; }
  };
  $('#resumeText').addEventListener('input', () => {
    if (!flow.profileReady) return;
    flow.profileReady = false; $('#candidateEditor').hidden = true; $('#parseBtn').textContent = '重新分析简历';
    flow.recommendationsLoaded = false; invalidateFrom(1);
  });
  function fillCandidate(candidate) {
    state.session.candidate = candidate; $('#candidateRole').value = candidate.target_role || '';
    $('#candidateSkills').value = (candidate.skills || []).join('\n'); $('#candidateProjects').value = (candidate.projects || []).join('\n');
    $('#candidateSummary').textContent = `${candidate.skills?.length || 0} 项技能 · ${candidate.projects?.length || 0} 条项目证据`;
    $('#candidateEditor').hidden = false; flow.profileReady = true; $('#parseBtn').textContent = '确认资料并继续 →';
  }
  async function analyzeCandidate(skip = false) {
    const button = $('#parseBtn'); setButton(button, 'loading', skip ? '正在准备通用画像…' : '正在分析…');
    showLoader('#profileLoader', ['读取资料', '提取经历与技能', '生成可编辑结果'], 0);
    try {
      const result = await api(`/api/sessions/${state.session.id}/profiles/candidate`, json('POST', {text: $('#resumeText').value, skip}));
      showLoader('#profileLoader', ['读取资料', '提取经历与技能', '生成可编辑结果'], 2); fillCandidate(result.candidate);
      $('#uploadStatus').textContent = result.warning || (skip ? '已使用通用画像，个性化程度会降低。' : `${result.analysis_mode === 'ai' ? 'AI' : '本地'}分析完成，请直接修正结果。`);
      setButton(button, 'success', '确认资料并继续 →'); setTimeout(() => hideLoader('#profileLoader'), 450);
      if (skip) { complete(1); show('jobSetup'); }
    } catch (error) { hideLoader('#profileLoader'); $('#uploadStatus').classList.add('error'); $('#uploadStatus').textContent = error.message; setButton(button, 'error', '重新分析'); }
  }
  $('#parseBtn').onclick = () => { if (flow.profileReady) { captureCandidate(); complete(1); show('jobSetup'); } else analyzeCandidate(false); };
  $('#skipResume').onclick = () => analyzeCandidate(true);
  function captureCandidate() {
    const candidate = state.session.candidate;
    candidate.target_role = $('#candidateRole').value.trim() || '通用候选人'; candidate.skills = lines($('#candidateSkills').value); candidate.projects = lines($('#candidateProjects').value);
  }
  $('#candidateEditor').addEventListener('input', () => invalidateFrom(1));

  $$('input[name=jobMode]').forEach(radio => radio.onchange = () => {
    const mode = $('input[name=jobMode]:checked').value; $('#jdText').disabled = mode !== 'custom'; $('#jdText').value = mode === 'custom' ? '' : presetJds[mode];
    flow.jobReady = false; $('#jobEditor').hidden = true; $('#confirmBtn').textContent = '分析岗位'; flow.recommendationsLoaded = false; invalidateFrom(2);
  });
  $('#jdText').addEventListener('input', () => { flow.jobReady = false; $('#jobEditor').hidden = true; $('#confirmBtn').textContent = '重新分析岗位'; flow.recommendationsLoaded = false; invalidateFrom(2); });
  $('#targetCompany').addEventListener('change', () => { flow.jobReady = false; $('#jobEditor').hidden = true; $('#confirmBtn').textContent = '重新分析岗位'; flow.recommendationsLoaded = false; invalidateFrom(2); });
  function fillJob(job) {
    state.session.job = job; $('#jobCompany').value = job.target_company || '通用'; $('#jobTitle').value = job.title;
    $('#jobRequirements').value = (job.requirements || []).map(item => item.name).join('\n'); $('#modeBadge').textContent = job.mode === 'validated' ? '正式模式' : '实验模式';
    $('#jobEditor').hidden = false; flow.jobReady = true; $('#confirmBtn').textContent = '确认岗位并继续 →';
  }
  async function analyzeJob(skip = false) {
    const button = $('#confirmBtn'), mode = $('input[name=jobMode]:checked').value, custom = mode === 'custom';
    setButton(button, 'loading', '正在分析…'); showLoader('#jobLoader', ['读取岗位描述', '提取职责与技能', '生成面试重点'], 0);
    try {
      const result = await api(`/api/sessions/${state.session.id}/profiles/job`, json('POST', {text: $('#jdText').value, use_builtin_job: !custom, builtin_job: custom ? null : mode, target_company: $('#targetCompany').value, skip}));
      fillJob(result.job); if (result.candidate) state.session.candidate = result.candidate;
      showLoader('#jobLoader', ['读取岗位描述', '提取职责与技能', '生成面试重点'], 2); setButton(button, 'success', '确认岗位并继续 →');
      if (result.warning) toast(result.warning); setTimeout(() => hideLoader('#jobLoader'), 450);
      if (skip) { complete(2); show('bankSetup'); }
    } catch (error) { hideLoader('#jobLoader'); setButton(button, 'error', '重新分析'); toast(error.message); }
  }
  $('#confirmBtn').onclick = () => { if (flow.jobReady) { captureJob(); complete(2); show('bankSetup'); } else analyzeJob(false); };
  $('#skipJob').onclick = () => analyzeJob(true);
  function captureJob() {
    const job = state.session.job; job.target_company = $('#jobCompany').value || $('#targetCompany').value || '通用'; job.title = $('#jobTitle').value.trim() || '通用岗位';
    job.requirements = lines($('#jobRequirements').value).map((name, i) => ({name, evidence: '用户确认', priority: Math.max(3, 5 - Math.floor(i / 2))}));
  }
  $('#jobEditor').addEventListener('input', () => { flow.recommendationsLoaded = false; invalidateFrom(2); });

  async function loadRecommendations() {
    if (flow.recommendationsLoaded || !state.session?.candidate || !state.session?.job) return;
    try {
      const result = await api(`/api/sessions/${state.session.id}/bank-recommendations`); flow.recommendationsLoaded = true;
      if (!result.recommendations.length) return;
      const names = Object.fromEntries((state.banks || []).map(bank => [bank.id, bank.name]));
      $('#bankRecommendations').innerHTML = `<b>${result.mode === 'ai' ? 'AI 推荐' : '匹配建议'}</b>　${result.recommendations.slice(0, 3).map(item => `${esc(names[item.bank_id] || '题库')}：${esc(item.reason)}`).join('；')}`;
      $('#bankRecommendations').hidden = false;
    } catch (error) { $('#bankRecommendations').textContent = `推荐暂不可用：${error.message}。你仍可手动选择题库。`; $('#bankRecommendations').hidden = false; }
  }
  $('#manageBanks').onclick = () => openBanks('bankSetup'); $('#welcomeBanks').onclick = () => openBanks('welcome');
  $('#bankSelection').addEventListener('change', () => invalidateFrom(3));
  $('#bankSelection').addEventListener('click', event => { if (event.target.closest('[data-move]')) invalidateFrom(3); });
  $('#practiceMode').addEventListener('change', () => invalidateFrom(3)); $('#allowRepeats').addEventListener('change', () => invalidateFrom(3));
  $('#bankNextBtn').onclick = async () => {
    const button = $('#bankNextBtn'); captureCandidate(); captureJob();
    const job = state.session.job; job.selected_bank_ids = [...state.bankOrder]; job.use_user_question_bank = !!state.bankOrder.length; job.practice_mode = $('#practiceMode').value; job.allow_repeats = $('#allowRepeats').checked;
    if (job.practice_mode === 'specialized' && !job.selected_bank_ids.length) { toast('专项模式需要先选择至少一个题库'); return; }
    setButton(button, 'loading', '正在生成计划…');
    try {
      state.session = await api(`/api/sessions/${state.session.id}/profiles`, json('PUT', {candidate: state.session.candidate, job})); renderPlan();
      const advice = await api(`/api/sessions/${state.session.id}/plan/advice`).catch(() => null);
      if (advice?.advice) { $('#planAdvice').innerHTML = `<b>${advice.mode === 'ai' ? 'AI 覆盖建议' : '计划提示'}</b>　${esc(advice.advice)}`; $('#planAdvice').hidden = false; }
      complete(3); setButton(button, 'success', '计划已生成'); show('planSetup');
    } catch (error) { setButton(button, 'error', '重新生成计划'); toast(error.message); }
  };
  $('#planNextBtn').onclick = () => { if (!state.session?.plan?.topics?.length) { toast('当前没有可用题目，请返回调整题库或重复练习设置'); return; } complete(4); show('ready'); };

  function updateCheck(name, status, message) {
    checks[name] = status;
    const chip = $(`#${name}State`), card = $(`[data-check="${name}"]`), result = $(`#${name}Result`);
    if (chip) { chip.className = `status-chip ${status === 'pass' || status === 'skipped' ? 'pass' : status === 'checking' ? 'checking' : status === 'attention' ? 'attention' : ''}`; chip.textContent = labels[status]; }
    if (card) { card.classList.remove('active', 'pass', 'attention'); if (status === 'checking') card.classList.add('active'); if (status === 'pass' || status === 'skipped') card.classList.add('pass'); if (status === 'attention') card.classList.add('attention'); }
    if (result && message) result.textContent = message;
    updateGate();
  }
  function prepareDebugPage() {
    if (flow.aiMode === 'local') { checks.model = 'skipped'; updateCheck('model', 'skipped', '本地模式：不使用 AI 追问和 AI 报告，模型检测已跳过。'); $('#modelBtn').hidden = true; $('#editAiFromDebug').textContent = '连接 AI'; }
    else { $('#modelBtn').hidden = false; if (checks.model === 'skipped') updateCheck('model', 'idle', '使用正式面试的连接发送合成测试数据。'); }
    updateGate();
  }
  async function checkCamera() {
    updateCheck('camera', 'checking', '正在请求摄像头权限…');
    const ok = await ensureCamera();
    if (ok) { cameraBypassed = false; $('#cameraBypass').hidden = true; updateCheck('camera', 'pass', '摄像头可用；画面仅在本地预览，不上传、不分析。'); }
    else { $('#cameraBypass').hidden = false; updateCheck('camera', 'attention', '未检测到可用画面。请允许权限、检查占用后重试，或确认无摄像头继续。'); }
    return ok;
  }
  async function checkMic() {
    updateCheck('mic', 'checking', '请对着麦克风说一句话…'); let stream, context;
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      context = new (window.AudioContext || window.webkitAudioContext)(); const analyser = context.createAnalyser(); analyser.fftSize = 256; context.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount); let peak = 0, frames = 0;
      await new Promise(resolve => { const sample = () => { analyser.getByteTimeDomainData(data); const level = Math.max(...data.map(value => Math.abs(value - 128))) / 128; peak = Math.max(peak, level); $('#micLevel').style.width = `${Math.min(100, level * 320)}%`; if (++frames > 75) resolve(); else requestAnimationFrame(sample); }; sample(); });
      if (peak < .012) { updateCheck('mic', 'attention', '已取得权限，但没有检测到有效声音。请确认输入设备并重试。'); return false; }
      updateCheck('mic', 'pass', '麦克风与声音输入正常。'); return true;
    } catch (error) { updateCheck('mic', 'attention', '麦克风不可用。请允许浏览器权限并检查输入设备。'); return false; }
    finally { stream?.getTracks().forEach(track => track.stop()); context?.close(); setTimeout(() => $('#micLevel').style.width = '0', 500); }
  }
  async function checkSpeech() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) { speechFallback = true; updateCheck('speech', 'pass', '浏览器不支持语音识别，已自动切换为文字输入。'); $('#speechTranscript').textContent = '已切换文字输入'; return true; }
    updateCheck('speech', 'checking', '正在聆听测试语句…'); $('#speechTranscript').textContent = '请开始朗读';
    return new Promise(resolve => {
      const recognition = new Recognition(); let settled = false, text = '';
      const finishCheck = ok => { if (settled) return; settled = true; clearTimeout(timer); try { recognition.stop(); } catch {} if (ok) { speechFallback = false; updateCheck('speech', 'pass', '语音识别正常，正式面试会实时显示文字。'); } else { speechFallback = true; updateCheck('speech', 'pass', '未完成语音识别测试，已自动切换为文字输入。'); $('#speechTranscript').textContent = text || '已切换文字输入'; } resolve(true); };
      recognition.lang = 'zh-CN'; recognition.interimResults = true; recognition.onresult = event => { text = [...event.results].map(result => result[0].transcript).join(''); $('#speechTranscript').textContent = text; if (text.replace(/\s/g, '').length >= 5) finishCheck(true); }; recognition.onerror = () => finishCheck(false); recognition.onend = () => finishCheck(text.length >= 5);
      const timer = setTimeout(() => finishCheck(false), 9000); try { recognition.start(); } catch { finishCheck(false); }
    });
  }
  async function checkModel() {
    if (flow.aiMode === 'local') { updateCheck('model', 'skipped', '本地模式：模型检测已跳过，本场不会使用 AI 追问和 AI 报告。'); return true; }
    updateCheck('model', 'checking', '正在发送不含个人资料的合成诊断…');
    try { const result = await api(`/api/sessions/${state.session.id}/ai/diagnostics`, {method: 'POST'}); updateCheck('model', 'pass', `${result.model} 可返回追问与评价字段 · ${result.latency_ms} ms`); return true; }
    catch (error) { updateCheck('model', 'attention', `${error.message}。请修改 AI 配置或切换本地模式。`); return false; }
  }
  function updateGate() {
    const cameraOk = checks.camera === 'pass' || cameraBypassed, micOk = checks.mic === 'pass', speechOk = checks.speech === 'pass', modelOk = checks.model === 'pass' || (flow.aiMode === 'local' && checks.model === 'skipped');
    const done = Object.values(checks).filter(value => ['pass', 'skipped'].includes(value)).length; $('#debugProgressText').textContent = `${done} / 4 已处理`; $('#debugProgressBar').style.width = `${done * 25}%`;
    const blockers = []; if (!cameraOk) blockers.push('摄像头未通过或尚未确认无摄像头继续'); if (!micOk) blockers.push('麦克风需要通过'); if (!speechOk) blockers.push('语音识别尚未处理'); if (!modelOk) blockers.push('大模型需要通过或切换本地模式');
    $('#startBlockers').textContent = blockers.length ? blockers.join('；') : '全部开始条件已满足。'; $('#durationCheck').checked = !blockers.length; $('#startBtn').disabled = !!blockers.length;
  }
  $('#cameraBtn').onclick = checkCamera; $('#micBtn').onclick = checkMic; $('#speechBtn').onclick = checkSpeech; $('#modelBtn').onclick = checkModel;
  $('#runAllChecks').onclick = async () => { const button = $('#runAllChecks'); setButton(button, 'loading', '正在检测…'); const tasks = [['camera', '检查摄像头', checkCamera], ['mic', '检查麦克风', checkMic], ['speech', '检查语音识别', checkSpeech], ['model', '检查大模型', checkModel]]; $('#debugLoader').hidden = false; for (let i = 0; i < tasks.length; i++) { showLoader('#debugLoader', tasks.map(task => task[1]), i); await tasks[i][2](); } hideLoader('#debugLoader'); setButton(button, Object.values(checks).some(value => value === 'attention') ? 'error' : 'success', Object.values(checks).some(value => value === 'attention') ? '部分项目需要处理' : '全部检测完成'); };
  $('#cameraBypass').onclick = () => openModal();
  function openModal() { lastFocus = document.activeElement; $('#confirmModal').hidden = false; $('.modal-card').focus(); }
  function closeModal() { $('#confirmModal').hidden = true; lastFocus?.focus(); }
  $$('[data-modal-close]').forEach(element => element.onclick = closeModal);
  $('#modalConfirm').onclick = () => { cameraBypassed = true; updateCheck('camera', 'pass', '已确认本场无摄像头继续；正式面试将显示无摄像头状态。'); closeModal(); };
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('#confirmModal').hidden) closeModal(); });
  $('#editAiFromDebug').onclick = () => { complete(0); show('aiSetup'); };
  $('#voiceBtn').onclick = () => { const voice = preferredFemaleVoice(); $('#speechResult').textContent = voice ? `正在使用：${voice.name}` : '使用浏览器默认声音'; speak('你好，我是林老师。接下来我们进行模拟面试。'); };
  $('#startBtn').onclick = async event => { if ($('#startBtn').disabled) return; complete(5); flow.completed.add(6); renderFlowIndex(); await oldStartHandler.call(event.currentTarget, event); };

  const aiInputs = ['#aiBaseUrl', '#aiRealtimeUrl', '#aiApiKey', '#aiModel', '#aiWireApi', '#aiTimeout', '#aiStream'];
  aiInputs.forEach(selector => $(selector).addEventListener('input', () => { if (flow.aiMode && !$('#aiFields').hidden) { flow.aiMode = null; $('#aiNextBtn').disabled = true; $('#aiConnectedSummary').hidden = true; invalidateModelCheck(); } }));
  renderFlowIndex(); updateGate();
})();
