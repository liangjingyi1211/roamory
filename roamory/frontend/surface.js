"use strict";
// Keep Streamlit's iframe as the event bridge, but render into the same-origin
// parent document so native window scrolling and CSS sticky share one viewport.
window.RoamorySurface = (() => {
  const localDocument = document;
  const frame = window.frameElement;
  const scrollInset = root => {
    const maps = root.querySelector(".geo-column").getBoundingClientRect();
    const journey = root.querySelector(".journey-content").getBoundingClientRect();
    return maps.right <= journey.left + 1 ? 16 : Math.ceil(maps.height + 16);
  };
  if (window.parent === window || !frame) {
    return {document:localDocument, view:window, root:localDocument.querySelector(".app-shell"), mounted:false, getInset:()=>scrollInset(localDocument), refreshInset(){}};
  }
  const parentDocument = frame.ownerDocument;
  const host = parentDocument.createElement("div");
  host.className = "roamory-surface";
  // Scope the existing stylesheet to this surface. Keep Maps in light DOM so
  // the SDK's own .gm-* styles and controls work in the parent document too.
  const scopedRules = rules => [...rules].map(rule=>{
    if (rule.selectorText) {
      const selectors=rule.selectorText.split(",").map(selector=>selector.trim())
        .filter(selector=>selector!=="html"&&selector!=="body")
        .map(selector=>selector===":root"?".roamory-surface":`.roamory-surface ${selector}`);
      return selectors.length?`${selectors.join(",")} {${rule.style.cssText}}`:"";
    }
    if (rule.cssRules) return `${rule.cssText.slice(0,rule.cssText.indexOf("{"))}{${scopedRules(rule.cssRules)}}`;
    return rule.cssText;
  }).join("\n");
  for (const sheet of localDocument.styleSheets) {
    const style = parentDocument.createElement("style");
    style.textContent = scopedRules(sheet.cssRules);
    host.append(style);
  }
  const skip = localDocument.querySelector(".skip-link");
  const root = localDocument.querySelector(".app-shell");
  host.append(skip, root);
  const previous = frame.parentElement.querySelector(":scope > .roamory-surface");
  if (previous) previous.replaceWith(host); else frame.after(host);
  frame.classList.add("roamory-component-frame");
  frame.setAttribute("aria-hidden", "true");
  frame.setAttribute("tabindex", "-1");
  const refreshInset = () => {
    const inset = scrollInset(host);
    parentDocument.documentElement.style.setProperty("--roamory-map-inset", `${inset}px`);
  };
  // Google Maps may inject styles into the iframe where the SDK was loaded.
  // Mirror those late styles into the document containing the actual map.
  const initialStyles = new Set(localDocument.querySelectorAll("style"));
  const sdkStyles = new Map();
  const sdkObserver = new MutationObserver(()=>{
    for (const source of localDocument.head.querySelectorAll("style")) {
      if (initialStyles.has(source)) continue;
      let target = sdkStyles.get(source);
      if (!target) {target=parentDocument.createElement("style");sdkStyles.set(source,target);parentDocument.head.append(target);}
      if (target.textContent!==source.textContent) target.textContent=source.textContent;
    }
    for (const [source,target] of sdkStyles) if (!source.isConnected) {target.remove();sdkStyles.delete(source);}
  });
  sdkObserver.observe(localDocument.head,{childList:true,subtree:true,characterData:true});
  const observer = new MutationObserver(() => {
    if (!frame.isConnected) {
      host.remove();
      observer.disconnect();
      sdkObserver.disconnect();
      for (const style of sdkStyles.values()) style.remove();
      if (!parentDocument.querySelector(".roamory-surface")) parentDocument.documentElement.style.removeProperty("--roamory-map-inset");
    }
  });
  observer.observe(parentDocument.body, {childList:true, subtree:true});
  // Preserve the existing DOM API used by app.js and place-details.js. SDK
  // scripts still load in the iframe, where their callbacks and credentials live.
  const surfaceDocument = {
    createElement:tag=>parentDocument.createElement(tag),
    getElementById:id=>host.querySelector(`#${CSS.escape(id)}`),
    querySelector:selector=>host.querySelector(selector),
    querySelectorAll:selector=>host.querySelectorAll(selector),
    body:root,
    documentElement:parentDocument.documentElement,
    head:localDocument.head,
  };
  skip.addEventListener("click", event=>{
    event.preventDefault();
    const timeline = host.querySelector("#timeline");
    const top = timeline.getBoundingClientRect().top + window.parent.scrollY - scrollInset(host);
    timeline.focus({preventScroll:true});
    window.parent.scrollTo({top, behavior:"instant"});
  });
  refreshInset();
  return {document:surfaceDocument, view:window.parent, root, mounted:true, getInset:()=>scrollInset(host), refreshInset};
})();
