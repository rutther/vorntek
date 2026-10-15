const scope = ".flow-crm-root";
export function crmCssScope() {
  return {
    postcssPlugin: "flow-crm-css-scope",
    Rule(rule) {
      let parent = rule.parent;
      while (parent) {
        if (parent.type === "atrule" && /keyframes$/i.test(parent.name)) return;
        parent = parent.parent;
      }
      rule.selectors = rule.selectors.map((selector) => {
        if (selector.startsWith(scope)) return selector;
        if (selector === ":root" || selector === "html" || selector === "body") return scope;
        return `${scope} ${selector}`;
      });
    },
  };
}
