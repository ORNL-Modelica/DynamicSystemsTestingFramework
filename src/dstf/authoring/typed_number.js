// A number field the server cannot overwrite while it is typed in: what the
// field shows is what was typed, until it loses focus; a server value is
// taken only while the field is not being typed in. (The passthrough is
// NiceGUI's own, elements/input.js.)
//
// And a PREVIEW (D149): a value the server shows in the field in place of
// what it holds, staging nothing — a handle held on the plot, the field
// following it. `preview` is that value (null: none) and `revision` the
// number of the drag it is of. It is shown whatever the field's typing
// state — a field typed in and then left for the plot still has the focus
// (Plotly's press takes none), and it must follow — except in a field typed
// in SINCE that drag began: what a person types is never painted over.
export default {
  template: `
    <q-input ref="qRef" v-bind="$attrs" :model-value="display" @update:model-value="typed" @blur="typing = false" @wheel="wheel">
      <template v-for="(_, slot) in $slots" v-slot:[slot]="slotProps">
        <slot :name="slot" v-bind="slotProps || {}" />
      </template>
    </q-input>
  `,
  inheritAttrs: false,
  props: { modelValue: null, preview: { default: null }, revision: { default: 0 } },
  emits: ["update:modelValue"],
  data() {
    return { shown: this.modelValue, typing: false, typedIn: null };
  },
  computed: {
    display() {
      const previewing = this.preview !== null && this.preview !== undefined;
      return previewing && this.typedIn !== this.revision ? this.preview : this.shown;
    },
  },
  watch: {
    modelValue(value) {
      if (!this.typing) this.shown = value;
    },
  },
  methods: {
    typed(value) {
      this.typing = true;
      this.typedIn = this.revision;
      this.shown = value;
      this.$emit("update:modelValue", value);
    },
    // A focused `type=number` input steps by 1 per wheel notch and keeps the
    // page from scrolling: scrolling on with the pointer still over the
    // field just typed in turned 0.3 into 3.3, staged. Left before the
    // browser acts on the wheel, the input steps nothing and the page
    // scrolls. (The arrow keys' step is untouched.)
    wheel(event) {
      if (event.target === document.activeElement) event.target.blur();
    },
  },
};
