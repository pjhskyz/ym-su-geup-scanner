/* 검색창에 글자를 치는 동안 화면을 다시 그려도 입력 중인 <input>은 그대로 둔다.
   input 요소를 새로 만들면 한글 조합(IME)이 끊겨 'ㄹㅗㅂㅗㅅ'처럼 자모가 풀려 들어간다.
   입력창에서 root까지 올라가며 그 경로 밖의 형제 노드만 새 내용으로 바꾼다.
   반환값: true면 처리 완료, false면 호출한 쪽에서 root.innerHTML로 전체를 다시 그려야 한다. */
(function () {
  'use strict';
  window.patchKeepingInput = function (root, html, inputId) {
    if (!root || typeof document.createElement !== 'function') return false;
    const live = root.querySelector('#' + inputId);
    if (!live || document.activeElement !== live) return false;
    const template = document.createElement('template');
    template.innerHTML = html;
    const fresh = template.content.querySelector('#' + inputId);
    if (!fresh) return false;
    // 경로의 깊이와 태그가 같을 때만 부분 교체한다(탭이 바뀌어 구조가 달라지면 전체 다시 그리기).
    const path = [];
    for (let a = live, b = fresh; a !== root; a = a.parentNode, b = b.parentNode) {
      if (!a || !b || !a.parentNode || !b.parentNode || a.nodeName !== b.nodeName) return false;
      path.push([a, b]);
    }
    const top = path[path.length - 1];
    if (!top || top[1].parentNode !== template.content) return false;
    path.forEach(([a, b], depth) => {
      const liveParent = a.parentNode, freshParent = b.parentNode;
      if (depth < path.length - 1) [...freshParent.attributes].forEach(attr => { if (liveParent.getAttribute(attr.name) !== attr.value) liveParent.setAttribute(attr.name, attr.value); });
      while (a.previousSibling) liveParent.removeChild(a.previousSibling);
      while (a.nextSibling) liveParent.removeChild(a.nextSibling);
      const before = [];
      for (let node = freshParent.firstChild; node && node !== b; node = node.nextSibling) before.push(node);
      before.forEach(node => liveParent.insertBefore(node, a));
      while (b.nextSibling) liveParent.appendChild(b.nextSibling);
    });
    return true;
  };
})();
