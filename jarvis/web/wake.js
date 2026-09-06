/* ==========================================================================
   Wake-word matching.
   Kept apart from the HUD because it is pure string logic with no DOM, which
   makes it the one piece of the voice stack that can be tested properly.
   Loaded as a plain script (sets window.JarvisWake) and read directly by
   tests/test_wake.js.
   ========================================================================== */

(function (root) {
  'use strict';

  /* Browser speech recognition mangles "Jarvis" in fairly predictable ways --
     it is an uncommon word, so the recogniser reaches for real ones it knows.
     These are the substitutions that actually turn up.

     The asymmetry matters: a false positive just opens a capture window that
     times out harmlessly, while a false negative means Jarvis ignored you.
     So this errs towards matching. */
  var VARIANTS = [
    'j[ae]rv[iu]ss?',      // jarvis, jervis, jarvus, jarviss
    'jarv[ie]ce',          // jarvice
    'javis',               // dropped r
    'jarv',                // clipped short
    'jar\\s?vis',           // split into two words
    '[cdghm]h?arv[iu]s',   // carvis, charvis, darvis, garvis, harvis, marvis
    'travis',              // the commonest real-word substitution
    'tarvis'
  ];

  var WAKE = new RegExp('\\b(' + VARIANTS.join('|') + ')\\b', 'i');

  /* Lead-ins that are not part of the request. */
  var PREAMBLE = /^\s*(hey|hi|hello|ok|okay|yo|um|uh|so)\b[\s,]*/i;

  /** Was Jarvis addressed at all? */
  function isWake(text) {
    return WAKE.test(text || '');
  }

  /** Remove the wake word and any lead-in, leaving the actual request. */
  function strip(text) {
    return (text || '')
      .replace(PREAMBLE, '')
      .replace(WAKE, '')
      .replace(/^[\s,.!?-]+/, '')
      .trim();
  }

  /** The name and nothing else -- a summons rather than a request. */
  function nameOnly(text) {
    return isWake(text) && strip(text).length < 2;
  }

  root.JarvisWake = { WAKE: WAKE, PREAMBLE: PREAMBLE, VARIANTS: VARIANTS, isWake: isWake, strip: strip, nameOnly: nameOnly };
})(typeof globalThis !== 'undefined' ? globalThis : this);
