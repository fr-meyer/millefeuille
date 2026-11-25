/** Explicit native identifiers. Reads no config/auth store itself. */
const whole = (pattern, value) => typeof value === 'string' && pattern.exec(value)?.[0] === value;
const canonicalAgent = value => whole(/^[a-z0-9][a-z0-9_-]{0,63}$/, value);
export function configuredProfile() {
  const profile = process.env.MILLEFEUILLE_OPENCLAW_PROFILE;
  if (!whole(/^openai:[A-Za-z0-9._+@-]{1,240}$/, profile)) throw new Error('native-profile-configuration-required');
  return profile;
}
export function configuredAgent(cfg) {
  const agent = process.env.MILLEFEUILLE_OPENCLAW_AGENT;
  if (!canonicalAgent(agent)) throw new Error('native-agent-configuration-required');
  const agents = cfg?.agents;
  if (!agents || typeof agents !== 'object' || Array.isArray(agents)) throw new Error('native-agent-not-explicitly-configured');
  let ids;
  if (Object.hasOwn(agents, 'entries') && agents.entries !== undefined) {
    const entries = agents.entries;
    if (!entries || typeof entries !== 'object' || Array.isArray(entries)) throw new Error('native-agent-not-explicitly-configured');
    ids = Object.keys(entries);
    if (!ids.every(id => entries[id] && typeof entries[id] === 'object' && !Array.isArray(entries[id]))) throw new Error('native-agent-not-explicitly-configured');
  } else {
    if (!Object.hasOwn(agents, 'list') || !Array.isArray(agents.list)) throw new Error('native-agent-not-explicitly-configured');
    ids = agents.list.map(value => value?.id);
  }
  // Deliberately canonical-only: refuse normalization/coercion and ALL collision
  // opportunities, including noncanonical other agents that normalize to target.
  if (!ids.every(canonicalAgent) || new Set(ids).size !== ids.length || ids.filter(id => id === agent).length !== 1) throw new Error('native-agent-not-explicitly-configured');
  return agent;
}
