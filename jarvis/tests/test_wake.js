/* Wake-word matching checks. Run with: node tests/test_wake.js
   Loads web/wake.js directly, so it tests the code the browser actually runs. */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'wake.js'), 'utf8');
const sandbox = { globalThis: {} };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { isWake, strip, nameOnly } = sandbox.JarvisWake;

let fails = 0;
function check(cond, label) {
  console.log(`  ${cond ? 'ok  ' : 'FAIL'} ${label}`);
  if (!cond) fails++;
}

console.log('=== the name is heard, however it comes out ===');
// Real transcriptions the recogniser produces for "Jarvis".
const HEARD = [
  'jarvis', 'Jarvis', 'JARVIS', 'jervis', 'jarvus', 'jarviss',
  'javis', 'jarvice', 'jarvis', 'travis', 'charvis', 'harvis',
  'darvis', 'garvis', 'marvis', 'carvis', 'jar vis', 'jarv'
];
for (const word of HEARD) check(isWake(word), `hears "${word}"`);

console.log('\n=== addressed mid-sentence ===');
const ADDRESSED = [
  ['jarvis what is on my calendar', 'what is on my calendar'],
  ['Jarvis, book me thirty minutes with Dana', 'book me thirty minutes with Dana'],
  ['hey jarvis send that email', 'send that email'],
  ['ok jarvis, model me a telescope', 'model me a telescope'],
  ['Hello Jarvis - any unread mail?', 'any unread mail?'],
  ['um, jarvis, what time is it', 'what time is it'],
  ['travis research ergonomic keyboards', 'research ergonomic keyboards']
];
for (const [heard, want] of ADDRESSED) {
  const got = strip(heard);
  check(got === want, `"${heard}" -> "${got}"`);
}

console.log('\n=== the name on its own is a summons, not a request ===');
for (const summons of ['jarvis', 'Jarvis?', 'hey jarvis', 'Jarvis.', '  JARVIS  ', 'ok jarvis']) {
  check(nameOnly(summons), `"${summons}" is a bare summons`);
}
for (const request of ['jarvis what time is it', 'jarvis hi', 'hey jarvis go']) {
  check(!nameOnly(request), `"${request}" is a request, not a summons`);
}

console.log('\n=== ordinary speech does not wake him ===');
const QUIET = [
  'what is on my calendar',
  'send that email to dana',
  'the service was slow',          // "service" must not trigger
  'I drive a car with a driver',
  'the harvest was good this year',
  'javascript is a language',
  'she works in marketing',
  'the jar is on the shelf',
  'we need to observe the results',
  ''
];
for (const line of QUIET) check(!isWake(line), `ignores "${line}"`);

console.log('\n=== strip is safe on odd input ===');
check(strip(undefined) === '', 'undefined -> empty');
check(strip('') === '', 'empty -> empty');
check(strip('no name here') === 'no name here', 'untouched when not addressed');
check(nameOnly('') === false, 'empty is not a summons');

console.log('\n' + (fails === 0 ? 'ALL WAKE CHECKS PASSED' : `${fails} FAILURES`));
process.exit(fails ? 1 : 0);
