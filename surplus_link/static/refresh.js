// SurplusLink lightweight refresh: re-fetches the page every N seconds and swaps
// the innerHTML of the section[data-refresh-seconds] element. Vanilla JS only.
(function () {
  "use strict";

  var FAILURE_SUFFIX = " (refresh failed, showing last good data)";

  function currentStamp() {
    return document.getElementById("last-updated");
  }

  function markFailed() {
    var stamp = currentStamp();
    if (stamp && stamp.textContent.indexOf(FAILURE_SUFFIX) === -1) {
      stamp.textContent = stamp.textContent + FAILURE_SUFFIX;
    }
  }

  function refreshOnce(section, parser) {
    return fetch(window.location.href, {
      cache: "no-store",
      headers: { "X-Refresh": "1" }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.text();
      })
      .then(function (text) {
        var doc = parser.parseFromString(text, "text/html");
        var fresh = doc.querySelector("section[data-refresh-seconds]");
        if (!fresh) {
          throw new Error("Refresh section missing from response");
        }
        section.innerHTML = fresh.innerHTML;
        var freshStamp = doc.getElementById("last-updated");
        var stamp = currentStamp();
        if (stamp && freshStamp) {
          stamp.textContent = freshStamp.textContent;
        }
      })
      .catch(function () {
        markFailed();
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var section = document.querySelector("section[data-refresh-seconds]");
    if (!section) {
      return;
    }
    var seconds = parseInt(section.getAttribute("data-refresh-seconds"), 10);
    if (!seconds || seconds < 1) {
      seconds = 12;
    }
    var parser = new DOMParser();
    window.setInterval(function () {
      refreshOnce(section, parser);
    }, seconds * 1000);
  });
})();
