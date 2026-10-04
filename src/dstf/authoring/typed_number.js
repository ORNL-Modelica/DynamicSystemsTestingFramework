// A number field the server cannot overwrite while it is typed in: what the
// field shows is what was typed, until it loses focus; a server value is
// taken only while the field is not being typed in. (The passthrough is
// NiceGUI's own, elements/input.js.)
export default {
  template: `
    <q-input ref="qRef" v-bind="$attrs" :model-value="shown" @update:model-value="typed" @blur="typing = false">
      <template v-for="(_, slot) in $slots" v-slot:[slot]="slotProps">
        <slot :name="slot" v-bind="slotProps || {}" />
      </template>
    </q-input>
  `,
  inheritAttrs: false,
  props: { modelValue: null },
  emits: ["update:modelValue"],
  data() {
    return { shown: this.modelValue, typing: false };
  },
  watch: {
    modelValue(value) {
      if (!this.typing) this.shown = value;
    },
  },
  methods: {
    typed(value) {
      this.typing = true;
      this.shown = value;
      this.$emit("update:modelValue", value);
    },
  },
};
