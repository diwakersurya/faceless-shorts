// Every read and every change goes through here: one cache, one place for errors and refreshes.
import { MutationCache, QueryClient, useMutation, useQuery } from "@tanstack/react-query";
import type { RenderRequest, Settings } from "../shared/schema";
import { type Job, api } from "./api";
import { go } from "./route";
import { toast } from "./toast";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
  // A failed change is shown once, here, with the reason the server gave.
  mutationCache: new MutationCache({ onError: (e) => toast.bad(e.message) }),
});

// The server has no push channel, so progress is learned by asking again, only while a short is being made.
// ponytail: polling while busy; server-sent events would remove it, worth it only if this gets heavy.
const TICK = 1500;

export const useOptions = () => useQuery({ queryKey: ["options"], queryFn: api.options, staleTime: 30_000 });
export const useVideos = () => useQuery({ queryKey: ["videos"], queryFn: api.videos });

let lastState: Job["state"] | null = null;
/** The moment a render ends is noticed here, where the answer arrives: the list is refreshed and the person is told. */
async function readJob() {
  const answer = await api.job();
  const state = answer.job?.state ?? null;
  if (lastState === "running" && state && state !== "running") {
    queryClient.invalidateQueries({ queryKey: ["videos"] });
    if (state === "done") toast.ok("Your short is ready");
    if (state === "failed") toast.bad("The short could not be made. The reason is shown above the log.");
  }
  lastState = state;
  return answer;
}
export const useJob = () => useQuery({ queryKey: ["job"], queryFn: readJob, refetchInterval: (q) => (q.state.data?.job?.state === "running" ? TICK : false) });

const refreshJob = () => queryClient.invalidateQueries({ queryKey: ["job"] });

export const useStartRender = () => useMutation({ mutationFn: (request: RenderRequest) => api.start(request), onSuccess: refreshJob });

export const useCancelRender = () =>
  useMutation({
    mutationFn: api.cancel,
    onSuccess: async () => {
      await refreshJob();
      toast.ok("Stopped. Clips made so far are kept and reused next time.");
    },
  });

export const useDeleteVideo = () =>
  useMutation({
    mutationFn: api.deleteVideo,
    onSuccess: async () => {
      go({ video: null });
      await queryClient.invalidateQueries({ queryKey: ["videos"] });
      toast.ok("Short deleted");
    },
  });

export const useSaveSettings = () =>
  useMutation({
    mutationFn: (settings: Settings) => api.saveSettings(settings),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["options"] });
      toast.ok("Settings saved");
    },
  });
