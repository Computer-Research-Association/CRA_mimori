// NVIDIA 호환 채팅 경로의 전송·재시도 검증용 로컬 대역이다. 실제 모델 품질은 검증하지 않는다.
const http = require('http');
let mode = 'success';
let calls = [];
const model = 'minimaxai/minimax-m3.1-flash';
http.createServer(async (req, res) => {
  let body = req.body;
  if (!body) {
    let raw = '';
    for await (const part of req) raw += part;
    try { body = raw ? JSON.parse(raw) : {}; } catch { body = {}; }
  }
  const json = (status, value) => {
    res.writeHead(status, {'Content-Type': 'application/json'});
    res.end(JSON.stringify(value));
  };
  if (req.url === '/__control' && req.method === 'POST') {
    mode = body.mode;
    calls = [];
    return json(200, {mode});
  }
  if (req.url === '/__calls') return json(200, {calls});
  if (req.url === '/v1/models') return json(200, {object:'list', data:[{id:model, object:'model'}]});
  if (req.url !== '/v1/chat/completions' || req.method !== 'POST') {
    return json(501, {error: {message: '검증하지 않는 경로'}});
  }
  // 인증 헤더와 원문 프롬프트는 기록하지 않는다.
  calls.push({model:body.model, temperature:body.temperature, stream:body.stream, max_tokens:body.max_tokens, chat_template_kwargs:body.chat_template_kwargs});
  if (mode === 'unavailable') return json(503, {error: {message:'Service Unavailable'}});
  if (mode === 'retired') return json(410, {error:{message:'Model retired'}});
  const text = mode === 'empty' ? '' : '근거에 있는 설명입니다. [자료 1]';
  if (!body.stream) return json(200, {id:'test', object:'chat.completion', model, choices:[{index:0, message:{role:'assistant',content:text}, finish_reason:'stop'}]});
  res.writeHead(200, {'Content-Type':'text/event-stream'});
  for (const content of [text.slice(0,10), text.slice(10)]) {
    res.write('data: '+JSON.stringify({id:'test',object:'chat.completion.chunk',model,choices:[{index:0,delta:{role:'assistant',content},finish_reason:null}]})+'\n\n');
  }
  res.write('data: '+JSON.stringify({id:'test',object:'chat.completion.chunk',model,choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\n');
  res.end('data: [DONE]\n\n');
}).listen(process.env.PORT || 4617);
