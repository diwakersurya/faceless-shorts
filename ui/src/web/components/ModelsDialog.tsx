import { AlertDialog } from "@astryxdesign/core/AlertDialog";
import { AspectRatio } from "@astryxdesign/core/AspectRatio";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { EmptyState } from "@astryxdesign/core/EmptyState";
import { FileInput } from "@astryxdesign/core/FileInput";
import { Grid } from "@astryxdesign/core/Grid";
import { HStack } from "@astryxdesign/core/HStack";
import { Selector } from "@astryxdesign/core/Selector";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { Text } from "@astryxdesign/core/Text";
import { TextArea } from "@astryxdesign/core/TextArea";
import { TextInput } from "@astryxdesign/core/TextInput";
import { VStack } from "@astryxdesign/core/VStack";
import * as stylex from "@stylexjs/stylex";
import { ImageIcon, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { modelFields } from "../../shared/schema";
import { type MyModel, type Options, api } from "../api";
import { useAddModel, useDeleteModel, useEditModel, useMyModels } from "../queries";

const NARROW = "@media (max-width: 600px)";

const s = stylex.create({
  // A person's photo is cut to fit from the top, where the face usually is.
  photo: { width: "100%", height: "100%", objectFit: "cover", objectPosition: "top", borderRadius: "var(--radius-element)" },
  // The photo beside the fields while a model is made or edited.
  preview: { width: { default: 168, [NARROW]: 120 }, flexShrink: 0 },
  // Photo beside the fields; on a phone the photo goes above them.
  top: { display: "flex", gap: "var(--spacing-4)", alignItems: "start", flexDirection: { default: "row", [NARROW]: "column" } },
  // Where the photo will be, before one is chosen.
  empty: {
    width: "100%",
    height: "100%",
    display: "grid",
    placeItems: "center",
    border: "1px dashed var(--color-border)",
    borderRadius: "var(--radius-element)",
    color: "var(--color-text-secondary)",
  },
});

const PHOTO_TYPES = "image/jpeg,image/png,image/webp";
const MAX_PHOTO = 20_000_000; // the server refuses bigger uploads
const COLUMNS = { minWidth: 180 };

type View = { mode: "list" } | { mode: "add" } | { mode: "edit"; model: MyModel };
type Rules = Options["myModels"];

const kindLabel = (rules: Rules, kind: string) => rules.kinds.find((k) => k.value === kind)?.label ?? kind;

function ModelCard({ model, rules, onEdit, onDelete }: { model: MyModel; rules: Rules; onEdit: () => void; onDelete: () => void }) {
  return (
    <Card padding={3}>
      <VStack gap={2}>
        <AspectRatio ratio={1}>
          <img {...stylex.props(s.photo)} src={api.modelPhotoUrl(model)} alt={model.name} loading="lazy" />
        </AspectRatio>
        <VStack gap={0.5}>
          <Text weight="medium" maxLines={1}>
            {model.name}
          </Text>
          <Text type="supporting">{kindLabel(rules, model.kind)}</Text>
        </VStack>
        <Text size="sm" color="secondary" maxLines={3}>
          {model.description || "No description: engines that cannot see the photo only know what kind it is."}
        </Text>
        <HStack gap={1}>
          <Button label="Edit" size="sm" variant="secondary" onClick={onEdit} />
          <Button label="Delete" size="sm" variant="ghost" onClick={onDelete} />
        </HStack>
      </VStack>
    </Card>
  );
}

function ModelList({ rules, onAdd, onEdit, onClose }: { rules: Rules; onAdd: () => void; onEdit: (m: MyModel) => void; onClose: () => void }) {
  const models = useMyModels();
  const remove = useDeleteModel();
  const [deleting, setDeleting] = useState<MyModel | null>(null);
  const list = models.data?.models;
  return (
    <VStack gap={4} paddingInline={4} paddingBlockEnd={4}>
      <Text color="secondary">
        A person or an object from one photo. Choose them under Featuring when you make a short, and they appear in every scene.
      </Text>
      {models.isError ? (
        <Text color="secondary">Your models could not be loaded: {models.error.message}</Text>
      ) : !list ? (
        <Grid columns={COLUMNS} gap={3}>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} index={i} height={330} />
          ))}
        </Grid>
      ) : list.length === 0 ? (
        <EmptyState
          icon={<Users size={32} />}
          title="No models yet"
          description="Add a clear photo of one person or one object. It stays on this computer."
          actions={<Button label="Add a model" variant="primary" icon={<UserPlus size={16} />} onClick={onAdd} />}
        />
      ) : (
        <Grid columns={COLUMNS} gap={3}>
          {list.map((m) => (
            <ModelCard key={m.id} model={m} rules={rules} onEdit={() => onEdit(m)} onDelete={() => setDeleting(m)} />
          ))}
        </Grid>
      )}
      <HStack justify="end" gap={2}>
        <Button label="Close" variant="secondary" onClick={onClose} />
        {!!list?.length && <Button label="Add a model" variant="primary" icon={<UserPlus size={16} />} onClick={onAdd} />}
      </HStack>
      <AlertDialog
        isOpen={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={`Delete ${deleting?.name ?? ""}?`}
        description="Its photo is deleted from this computer. Shorts already made keep it; a script that names it makes those scenes without it."
        cancelLabel="Cancel"
        actionLabel="Delete"
        isActionLoading={remove.isPending}
        onAction={() => deleting && remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })}
      />
    </VStack>
  );
}

/** Making a model (photo, name, kind, description) or changing one (all but the photo: another photo is another model). */
function ModelForm({ rules, folder, model, onDone }: { rules: Rules; folder: string | undefined; model?: MyModel; onDone: () => void }) {
  const add = useAddModel();
  const edit = useEditModel();
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [name, setName] = useState(model?.name ?? "");
  const [kind, setKind] = useState(model?.kind ?? rules.kinds[0]?.value ?? "");
  const [description, setDescription] = useState(model?.description ?? "");
  const parsed = modelFields(rules).safeParse({ name, kind, description });
  const problem = (field: string) => (parsed.success ? undefined : parsed.error.issues.find((i) => i.path[0] === field)?.message);
  const saving = add.isPending || edit.isPending;
  const ready = parsed.success && (model !== undefined || photo !== null) && !saving;
  const choose = (files: File | File[] | null) => {
    const file = Array.isArray(files) ? (files[0] ?? null) : files;
    if (preview) URL.revokeObjectURL(preview);
    setPhoto(file);
    setPreview(file ? URL.createObjectURL(file) : null);
  };
  const shown = preview ?? (model ? api.modelPhotoUrl(model) : null);
  const writesDescription = !model && rules.describedByGemini && !description.trim();
  return (
    <form
      action={() => {
        if (!ready) return;
        if (model) edit.mutate({ id: model.id, fields: parsed.data }, { onSuccess: onDone });
        else add.mutate({ photo: photo!, fields: parsed.data }, { onSuccess: onDone });
      }}
    >
      <VStack gap={4} paddingInline={4} paddingBlockEnd={4}>
        <div {...stylex.props(s.top)}>
          <div {...stylex.props(s.preview)}>
            <AspectRatio ratio={3 / 4}>
              {shown ? (
                <img {...stylex.props(s.photo)} src={shown} alt="" />
              ) : (
                <div {...stylex.props(s.empty)}>
                  <ImageIcon size={28} />
                </div>
              )}
            </AspectRatio>
          </div>
          <VStack gap={3} width="100%">
            {model ? (
              <Text size="sm" color="secondary">
                The photo stays as it is. To use another photo, add a new model.
              </Text>
            ) : (
              <FileInput
                label="Photo"
                value={photo}
                onChange={choose}
                accept={PHOTO_TYPES}
                maxSize={MAX_PHOTO}
                mode={photo ? "input" : "dropzone"}
                description="One person or one object, clearly visible. For a person, a photo from the chest up with the face to the camera works best."
                isRequired
              />
            )}
            <TextInput
              label="Name"
              value={name}
              onChange={setName}
              placeholder="e.g. Ravi, or Red bike"
              autoComplete="off"
              hasAutoFocus={model !== undefined}
              status={name.trim() && problem("name") ? { type: "error", message: problem("name") } : undefined}
            />
            <Selector label="Kind" options={rules.kinds} value={kind} onChange={(v) => v && setKind(v)} />
          </VStack>
        </div>
        <TextArea
          label="How it looks"
          isOptional
          value={description}
          onChange={setDescription}
          rows={3}
          maxLength={rules.maxDescription}
          placeholder="e.g. late 20s, short curly black hair, round glasses, mustard yellow hoodie"
          description={
            rules.describedByGemini
              ? "For the engines that cannot see the photo. Leave it empty and Gemini writes it from the photo (free)."
              : "For the engines that cannot see the photo, which only get these words."
          }
          status={problem("description") ? { type: "error", message: problem("description") } : undefined}
        />
        {!model && (
          <Text size="sm" color="secondary">
            The photo is kept on this computer{folder ? ` in ${folder}` : ""}. Gemini images and video send it to Google when they use it. Use photos you have the right to use.
          </Text>
        )}
        <HStack justify="end" gap={2}>
          <Button label="Cancel" variant="secondary" onClick={onDone} isDisabled={saving} />
          <Button
            label={model ? "Save" : "Add the model"}
            type="submit"
            variant="primary"
            isDisabled={!ready}
            isLoading={saving}
            tooltip={writesDescription && saving ? "Gemini is writing its description from the photo" : undefined}
          />
        </HStack>
      </VStack>
    </form>
  );
}

/** Your models: the list, and making or changing one, in one dialog that changes its content instead of closing. */
export function ModelsDialog({ options, onClose }: { options: Options; onClose: () => void }) {
  const [view, setView] = useState<View>({ mode: "list" });
  const folder = useMyModels().data?.folder;
  const rules = options.myModels;
  const toList = () => setView({ mode: "list" });
  const title = view.mode === "list" ? "Your models" : view.mode === "add" ? "Add a model" : `Edit ${view.model.name}`;
  const close = (open: boolean) => {
    if (!open) onClose();
  };
  return (
    <Dialog isOpen onOpenChange={close} purpose="form" width={720}>
      <DialogHeader title={title} onOpenChange={close} />
      {view.mode === "list" ? (
        <ModelList rules={rules} onAdd={() => setView({ mode: "add" })} onEdit={(model) => setView({ mode: "edit", model })} onClose={onClose} />
      ) : (
        <ModelForm key={view.mode === "edit" ? view.model.id : "new"} rules={rules} folder={folder} model={view.mode === "edit" ? view.model : undefined} onDone={toList} />
      )}
    </Dialog>
  );
}
