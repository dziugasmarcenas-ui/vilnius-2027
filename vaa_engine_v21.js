/* Pure scoring engine for Vilnius 2027 v21. No DOM, no network, no storage.
 * The Python implementation in vaa_v21.py must produce byte-identical JSON
 * for the same inputs. Integer arithmetic is deliberate.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.V21Engine = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var STANCE = { agree: 2, lean_agree: 1, neutral: 0, lean_disagree: -1, disagree: -2 };
  var MIN_COVERAGE_PCT = 35;
  var CLOSE_MARGIN = 5;
  var MIN_RECOMMEND_SCORE = 50;

  function roundDiv(num, den) {
    if (!den) return null;
    return Math.floor((num + Math.floor(den / 2)) / den);
  }

  function pct(part, whole) {
    if (!whole) return 0;
    return Math.floor((part * 100 + Math.floor(whole / 2)) / whole);
  }

  function byScore(a, b) {
    if (a.score !== b.score) return b.score - a.score;
    if (a.coverage_pct !== b.coverage_pct) return b.coverage_pct - a.coverage_pct;
    return a.party_id < b.party_id ? -1 : a.party_id > b.party_id ? 1 : 0;
  }

  function confidenceFor(coveragePct, known) {
    if (coveragePct >= 70 && known >= 4) return 'high';
    if (coveragePct >= 50) return 'medium';
    return 'low';
  }

  function scoreV21(data, input) {
    input = input || {};
    if (input.selected_topics !== undefined && !Array.isArray(input.selected_topics)) {
      throw new Error('selected_topics must be an array');
    }
    var selected = (input.selected_topics || []).slice();
    var answers = input.answers || {};
    if (typeof answers !== 'object' || Array.isArray(answers)) {
      throw new Error('answers must be an object');
    }
    Object.keys(answers).forEach(function (id) {
      var value = answers[id];
      if (value === null || value === undefined) return;
      if (typeof value !== 'number' || !Number.isInteger(value) || value < -2 || value > 2) {
        throw new Error(id + ': answer must be -2, -1, 0, 1, 2 or null');
      }
    });
    var topTopic = input.top_topic && selected.indexOf(input.top_topic) >= 0 ? input.top_topic : selected[0];
    var topicById = {};
    (data.topics || []).forEach(function (t) { topicById[t.id] = t; });
    var statementById = {};
    (data.statements || []).forEach(function (s) { statementById[s.id] = s; });

    var selectedStatements = (data.statements || []).filter(function (s) {
      return selected.indexOf(s.topic_id) >= 0;
    });
    var selectedIds = selectedStatements.map(function (s) { return s.id; });
    var answeredCount = selectedIds.filter(function (id) {
      return answers[id] !== undefined && answers[id] !== null;
    }).length;
    var minimumAnswers = Math.min(2, selectedStatements.length);
    var enoughAnswers = answeredCount >= minimumAnswers && selected.length > 0;

    var partyRows = (data.parties || []).map(function (party) {
      var known = 0, answered = 0, num = 0, den = 0;
      var covered = {}, gapAnswered = {}, topicStats = {};
      selected.forEach(function (tid) {
        topicStats[tid] = { score: null, known: 0, answered: 0, coverage_pct: 0, _num: 0, _den: 0 };
      });

      selectedStatements.forEach(function (statement) {
        var user = answers[statement.id];
        if (user === undefined || user === null) return;
        answered += 1;
        var ts = topicStats[statement.topic_id];
        ts.answered += 1;
        var answer = (data.answers[party.id] || {})[statement.id] || {};
        var st = answer.stance;
        var interpretation = answer.interpretation || {};
        if (answer.scoreable !== true || answer.quote_verified !== true ||
            interpretation.supports_full_proposition !== true || STANCE[st] === undefined) {
          gapAnswered[statement.topic_id] = true;
          return;
        }
        var points = 100 - 25 * Math.abs(user - STANCE[st]);
        var weight = statement.topic_id === topTopic ? 2 : 1;
        num += points * weight;
        den += weight;
        known += 1;
        covered[statement.topic_id] = true;
        ts.known += 1;
        ts._num += points * weight;
        ts._den += weight;
      });

      selected.forEach(function (tid) {
        var ts = topicStats[tid];
        ts.score = roundDiv(ts._num, ts._den);
        ts.coverage_pct = pct(ts.known, ts.answered);
        delete ts._num;
        delete ts._den;
      });

      var coveragePct = pct(known, answered);
      var topicsCovered = selected.filter(function (tid) { return covered[tid]; });
      var gaps = selected.filter(function (tid) { return gapAnswered[tid] && !covered[tid]; });
      var minKnown = Math.min(3, answered);
      var eligible = enoughAnswers && scoreOk(den) && known >= minKnown && coveragePct >= MIN_COVERAGE_PCT &&
        topicsCovered.length >= Math.min(2, selected.length);
      if (!eligible) {
        // Keep the computed score for transparency only when there is evidence.
      }
      return {
        party_id: party.id,
        short_name: party.short_name,
        name: party.name,
        color: party.color,
        mayor_candidate: party.mayor_candidate || null,
        score: roundDiv(num, den),
        coverage_pct: coveragePct,
        known: known,
        answered: answered,
        eligible: eligible,
        confidence: confidenceFor(coveragePct, known),
        topics_covered: topicsCovered,
        gaps: gaps,
        topic_scores: topicStats,
        rank: null
      };
    });

    function scoreOk(den) { return den > 0; }

    var eligibleRows = partyRows.filter(function (p) { return p.eligible; }).sort(byScore);
    eligibleRows.forEach(function (p, index) { p.rank = index + 1; });
    var ineligible = partyRows.filter(function (p) { return !p.eligible; }).sort(function (a, b) {
      return a.party_id < b.party_id ? -1 : a.party_id > b.party_id ? 1 : 0;
    });
    var parties = eligibleRows.concat(ineligible);
    var top = eligibleRows.length ? eligibleRows[0] : null;
    var second = eligibleRows.length > 1 ? eligibleRows[1] : null;
    var shortlist = [];
    if (top) {
      shortlist = eligibleRows.filter(function (p) { return p.score >= top.score - CLOSE_MARGIN; }).slice(0, 3);
    }
    var status;
    if (!top) status = 'insufficient';
    else if (top.score < MIN_RECOMMEND_SCORE) status = 'no_match';
    else if (shortlist.length > 1 || top.coverage_pct < 50) status = 'close';
    else status = 'clear';
    var closeReason = null;
    if (status === 'close') {
      if (shortlist.length > 1 && top.coverage_pct < 50) closeReason = 'margin_and_coverage';
      else if (shortlist.length > 1) closeReason = 'margin';
      else closeReason = 'coverage';
    }

    var topicResults = selected.map(function (tid) {
      var topic = topicById[tid] || { id: tid, name: tid };
      var rows = partyRows.filter(function (p) {
        var ts = p.topic_scores[tid];
        return ts && ts.known > 0 && ts.score !== null;
      }).sort(function (a, b) {
        var as = a.topic_scores[tid], bs = b.topic_scores[tid];
        if (as.score !== bs.score) return bs.score - as.score;
        if (as.coverage_pct !== bs.coverage_pct) return bs.coverage_pct - as.coverage_pct;
        return a.party_id < b.party_id ? -1 : a.party_id > b.party_id ? 1 : 0;
      }).slice(0, 3).map(function (p) {
        var ts = p.topic_scores[tid];
        return {
          party_id: p.party_id,
          short_name: p.short_name,
          color: p.color,
          score: ts.score,
          known: ts.known,
          answered: ts.answered,
          coverage_pct: ts.coverage_pct
        };
      });
      var topicStatementCount = selectedStatements.filter(function (s) { return s.topic_id === tid; }).length;
      var best = rows.length ? rows[0] : null;
      var topicStatus = !best ? 'insufficient' :
        (best.known < Math.min(2, topicStatementCount) || best.coverage_pct < 50 ? 'thin' : 'clear');
      return {
        topic_id: tid,
        name: topic.name,
        status: topicStatus,
        parties: rows
      };
    });

    var shortlistIds = shortlist.map(function (p) { return p.party_id; });
    var mayorCandidates = partyRows.filter(function (p) { return p.mayor_candidate; }).map(function (p) {
      return { party_id: p.party_id, short_name: p.short_name, name: p.mayor_candidate };
    });
    var mayor = null;
    if (status === 'clear' && top && top.mayor_candidate) {
      mayor = { party_id: top.party_id, short_name: top.short_name, name: top.mayor_candidate };
    }
    var finalListsDate = (data.election && data.election.final_lists_date) || '2027-02-05';
    var preferenceVotes = (data.election && data.election.preference_votes) || 5;
    var notes = [];
    notes.push('2027 m. galutiniai sąrašai ir numeriai VRK dar nepaskelbti (numatoma iki ' + finalListsDate + ').');
    notes.push('Tai programų tekstų atitikimas, ne partijos, kandidato ar koalicijos rekomendacija.');
    notes.push('Rezultatas nevertina rinkimų kampanijos poveikio, galimų mandatų, koalicijų, kandidatų kompetencijos ar to, ar pažadas bus įgyvendintas.');
    notes.push('Nenaudok rezultato kaip strateginio balsavimo patarimo.');
    if (status === 'insufficient') {
      notes.push('Nepakanka patikimų, cituojamų pozicijų, kad būtų pagrindas išskirti sąrašą.');
    } else if (status === 'no_match') {
      notes.push('Geriausias turimas programų atitikimas nesiekia 50/100. Nepakanka pagrindo išskirti sąrašą; skaityk programas ir kandidatų sąrašus.');
    } else if (status === 'close') {
      if (closeReason === 'margin' || closeReason === 'margin_and_coverage') {
        notes.push('Keli sąrašai skiriasi mažiau nei 5 balais. Žiūrėk į tau svarbiausią temą, o ne į vieną bendrą skaičių.');
      }
      if (closeReason === 'coverage' || closeReason === 'margin_and_coverage') {
        notes.push('Viršutinio sąrašo aprėptis žema – trūksta pozicijų kai kuriais klausimais.');
      }
      notes.push('Vieno sąrašo neišskiriame; žemiau pateikti artimiausi programų atitikimai.');
    }
    if (status === 'clear' && top && !top.mayor_candidate) {
      notes.push('Šis sąrašas neturėjo registruoto mero kampanijos dalyvio; mero biuletenyje rinkis atskirai.');
    }
    if (mayorCandidates.length) {
      notes.push('Mero biuletenyje kol kas rodomi tik VRK registruoti mero kampanijos dalyviai; galutinis mero biuletenis dar nepaskelbtas.');
    }
    notes.push('Pirmumo balsų taisyklę nustato Rinkimų kodekso 143 straipsnis (iki ' + preferenceVotes + ' pirmumo balsų; mažesniame sąraše – mažiau). Oficialų biuletenio išdėstymą patikrink VRK pavyzdyje.');
    notes.push('Prieš balsuodami patikrinkite oficialų VRK biuletenį.');

    function partyMayor(pid) {
      var p = partyRows.filter(function (x) { return x.party_id === pid; })[0];
      return p ? (p.mayor_candidate || null) : null;
    }

    var topicPlan = topicResults.filter(function (r) { return r.parties.length; }).map(function (r) {
      return { topic_id: r.topic_id, party_id: r.parties[0].party_id, score: r.parties[0].score };
    });

    return {
      status: status,
      close_reason: closeReason,
      confidence: top ? top.confidence : 'none',
      selected_topics: selected,
      top_topic: topTopic || null,
      answered_count: answeredCount,
      minimum_answers: minimumAnswers,
      enough_answers: enoughAnswers,
      parties: parties,
      shortlist: status === 'close' || status === 'clear' ? shortlistIds : [],
      top: top ? top.party_id : null,
      lead: top && second ? top.score - second.score : null,
      topic_results: topicResults,
      plan: {
        council_list: status === 'clear' ? top.party_id : null,
        shortlist: status === 'close' || status === 'clear' ? shortlistIds : [],
        mayor: mayor,
        mayor_candidates: mayorCandidates,
        preference_votes: preferenceVotes,
        final_lists_date: finalListsDate,
        ballot_number: null,
        notes: notes,
        topic_plan: topicPlan,
        top_mayor: top ? partyMayor(top.party_id) : null
      }
    };
  }

  return { scoreV21: scoreV21, STANCE: STANCE, MIN_COVERAGE_PCT: MIN_COVERAGE_PCT };
});
