// Every read and every change goes through here: one cache, one place for errors and refreshes.
import { MutationCache, QueryClient, useMutation, useQuery } from "@tanstack/react-query";
import type { ModelFields, RenderRequest, ScriptRequest, Settings } from "../shared/schema";
import { type Job, api } from "./api";
import { go } from "./route";
import { toast } from "./toast";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
  mutationCache: new MutationCache({ onError: (e) => toast.bad(e.message) }),
});

const TICK = 1500;

export const useOptions = () => useQuery({ queryKey: ["options"], queryFn: api.options, staleTime: 30_000 });
export const useVideos = () => useQuery({ queryKey: ["videos"], queryFn: api.videos });
export const useMyModels = () => useQuery({ queryKey: ["models"], queryFn: api.myModels });

let lastState: Job["state"] | null = null;
let lastJobId: string | null = null;
async function readJob() {
  const answer = await api.job();
  const state = answer.job?.state ?? null;
  const id = answer.job?.id ?? null;
  if (lastState === "running" && state && (state !== "running" || id !== lastJobId)) {
    queryClient.invalidateQueries({ queryKey: ["videos"] });
    if (state === "done") toast.ok("Your short is ready");
    if (state === "failed") toast.bad("The short could not be made. The reason is shown above the log.");
  }
  lastState = state;
  lastJobId = id;
  return answer;
}
export const useJob = () =>
  useQuery({
    queryKey: ["job"],
    queryFn: readJob,
    refetchInterval: (q) => (q.state.data?.job?.state === "running" || (q.state.data?.queue?.length ?? 0) > 0 ? TICK : false),
  });

const refreshJob = () => queryClient.invalidateQueries({ queryKey: ["job"] });

export const useStartRender = () =>
  useMutation({
    mutationFn: (request: RenderRequest) => api.start(request),
    onSuccess: (res) => {
      refreshJob();
      if (res.queued) toast.ok("Added to render queue");
    },
  });

export const useDraftScript = () => useMutation({ mutationFn: (request: ScriptRequest) => api.draftScript(request) });
export const useUploadFile = () => useMutation({ mutationFn: (file: File) => api.uploadFile(file) });

export const useCancelRender = () =>
  useMutation({
    mutationFn: api.cancel,
    onSuccess: async () => {
      await refreshJob();
      toast.ok("Stopped. Clips made so far are kept and reused next time.");
    },
  });

export const useRemoveQueued = () =>
  useMutation({
    mutationFn: (id: string) => api.removeQueued(id),
    onSuccess: refreshJob,
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

const keepModels = (answer: Awaited<ReturnType<typeof api.myModels>>) => queryClient.setQueryData(["models"], answer);

export const useAddModel = () =>
  useMutation({
    mutationFn: ({ photo, fields }: { photo: File; fields: ModelFields }) => api.addModel(photo, fields),
    onSuccess: (answer) => {
      keepModels(answer);
      toast.ok(`${answer.changed?.name ?? "The model"} is ready to feature`);
    },
  });

export const useEditModel = () =>
  useMutation({
    mutationFn: ({ id, fields }: { id: string; fields: ModelFields }) => api.editModel(id, fields),
    onSuccess: (answer) => {
      keepModels(answer);
      toast.ok("Model saved");
    },
  });

export const useDeleteModel = () =>
  useMutation({
    mutationFn: api.deleteModel,
    onSuccess: (answer) => {
      keepModels(answer);
      toast.ok(`${answer.changed?.name ?? "The model"} deleted`);
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
