import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "@astryxdesign/theme-neutral/theme.css";
import "@astryxdesign/theme-neutral/fonts.css"; // font files ship in the package: nothing is fetched from outside
import { LayerProvider } from "@astryxdesign/core/Layer";
import { Theme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";
import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { queryClient } from "./queries";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Astryx defaults: the neutral theme, light or dark as the system is set. */}
    <Theme theme={neutralTheme}>
      <LayerProvider>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </LayerProvider>
    </Theme>
  </StrictMode>,
);
