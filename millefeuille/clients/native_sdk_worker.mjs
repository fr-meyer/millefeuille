import {configuredProfile} from './native_profile.mjs';
/** Owned one-call worker. Existing supported native SDK/auth route only.
 * Local process exit/reap is NOT graceful SDK teardown or remote cancellation.
 */
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {completeRequest, canonical, validateRequest, deadlinePolicy} from './sdk_science.mjs';
const requireHost = createRequire('/app/package.json');
const LIMIT = 2 * 1024 * 1024;
const rawWrite = process.stdout.write.bind(process.stdout);
process.stdout.write = () => true;
process.stderr.write = () => true;
const emit = value => new Promise((resolve, reject) => rawWrite(canonical(value)+String.fromCharCode(10), e => e ? reject(e) : resolve()));
const workerStarted=performance.now();
let timer = setTimeout(() => process.exit(124), 280000);
const diagnostic = {stage:'sdk-request-validation'};
let result;
try {
  if (process.argv.length !== 2 || process.env.MILLEFEUILLE_EXECUTION_PROFILE !== 'native-sdk-bridge/v1') throw new Error('profile');
  if (['NODE_OPTIONS','NODE_PATH','LD_PRELOAD','LD_AUDIT'].some(k => process.env[k])) throw new Error('startup-loader');
  const chunks=[]; let bytes=0;
  for await (const chunk of process.stdin) { bytes+=chunk.length; if(bytes>LIMIT) throw new Error('input-bound'); chunks.push(chunk); }
  const wire=Buffer.concat(chunks).toString('utf8');
  const request=JSON.parse(wire);
  validateRequest(request,wire);
  const policy=deadlinePolicy(request,'native-sdk-bridge/v1');
  if(policy.category){
    clearTimeout(timer);
    timer=setTimeout(()=>process.exit(124),Math.max(1,policy.owned_node_timeout_ms-(performance.now()-workerStarted)));
  }
  const selectedProfile = configuredProfile();
  diagnostic.stage='sdk-public-import';
  const sdk = name => import(pathToFileURL(requireHost.resolve('openclaw/plugin-sdk/'+name)).href);
  const {getRuntimeConfig} = await sdk('runtime-config-snapshot');
  const completion=await sdk('simple-completion-runtime');
  result=await completeRequest(request,completion,getRuntimeConfig(),selectedProfile,diagnostic,wire,null,'native-sdk-bridge/v1');
  const output=canonical(result);
  if(Buffer.byteLength(output)>LIMIT) throw new Error('output-bound');
  await emit(result);
  clearTimeout(timer);
  process.exit(0);
} catch (error) {
  const safe = {};
  const layers = {
    'sdk-public-import':'node-prepare',
    'sdk-request-validation':'node-request',
    'sdk-prepare-selected-oauth':'node-prepare',
    'sdk-single-science-completion':'node-result',
    'sdk-provider-result-validation':'node-result',
    'sdk-output-json-validation':'node-json',
    'sdk-usage-validation':'node-usage',
    'sdk-response-serialization':'node-serialization'
  };
  if (Object.hasOwn(layers, diagnostic.stage)) {
    safe.stage=diagnostic.stage;
    safe.layer=layers[diagnostic.stage];
  }
  for (const key of ['payload_count','response_count']) {
    if (Number.isSafeInteger(diagnostic[key]) && diagnostic[key]>=0 && diagnostic[key]<=1000000) safe[key]=diagnostic[key];
  }
  if (diagnostic.http_status===null || (Number.isInteger(diagnostic.http_status) && diagnostic.http_status>=100 && diagnostic.http_status<=599)) safe.http_status=diagnostic.http_status;
  if (typeof diagnostic.provider_request_id==='string' && /^(?:req|resp)_[A-Za-z0-9_-]{1,120}$/.test(diagnostic.provider_request_id)) safe.provider_request_id=diagnostic.provider_request_id;
  if (['stop','length','toolUse','error','aborted'].includes(diagnostic.stop_reason)) safe.stop_reason=diagnostic.stop_reason;
  if (diagnostic.usage && typeof diagnostic.usage==='object' && !Array.isArray(diagnostic.usage)) {
    const usage={};
    for (const key of ['input','output','cacheRead','cacheWrite','totalTokens']) {
      if (Number.isSafeInteger(diagnostic.usage[key]) && diagnostic.usage[key]>=0) usage[key]=diagnostic.usage[key];
    }
    if (Object.keys(usage).length) safe.usage=usage;
  }
  if(typeof diagnostic.caller_signal_aborted==='boolean')safe.caller_signal_aborted=diagnostic.caller_signal_aborted;
  if(['TimeoutError','AbortError','unknown'].includes(diagnostic.caller_signal_reason))safe.caller_signal_reason=diagnostic.caller_signal_reason;
  for(const key of ['elapsed_ms','completion_timeout_ms','caller_signal_timeout_ms','owned_node_timeout_ms','python_timeout_ms']){
    if(Number.isSafeInteger(diagnostic[key]) && diagnostic[key]>=0 && diagnostic[key]<=3600000)safe[key]=diagnostic[key];
  }
  const rd=error && typeof error==='object' ? Object.getOwnPropertyDescriptor(error,'rule') : null;
  const rule=rd && Object.hasOwn(rd,'value') ? rd.value : null;
  if (typeof rule==='string' && (/^node-gate-(00[1-9]|0[12][0-9])$/.test(rule) || ['node-request-identity','node-output-json'].includes(rule))) safe.rule=rule;
  await emit({error:'native-sdk-failed-no-retry',...safe,uncertain:true,retry:'never-automatically'});
  clearTimeout(timer);
  process.exit(1);
}
