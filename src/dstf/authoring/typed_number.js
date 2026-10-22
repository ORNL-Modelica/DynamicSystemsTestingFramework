// A number field the server cannot overwrite while it is typed in: what the
// field shows is what was typed, until it loses focus; a server value is
// taken only while the field is not being typed in. (The passthrough is
// NiceGUI's own, elements/input.js.)
//
// And a PREVIEW (D149): a value the server shows in the field in place of
// what it holds, staging nothing — a handle held on the plot, the field
// following it. `previewed` is that value (null: none) and `revision` the
// number of the drag it is of. It is shown whatever the field's typing
// state, except in a field typed in SINCE that drag began: what a person
// types is never painted over.
//
// The server sets it by calling `preview(value, revision)` on this
// component — never by a prop. A prop write resends the element, and
// NiceGUI renders the page's whole tree again for any element it is sent:
// with a 30-row table open that was a 50 to 77 ms task in the browser for
// every move of a held handle. A method call changes this component's own
// state, and it alone is drawn again.
export default {
  template: `
    <q-input ref="qRef" v-bind="$attrs" :model-value="display" @update:model-value="typed" @blur="typing = false" @wheel="wheel">
      <template v-for="(_, slot) in $slots" v-slot:[slot]="slotProps">
        <slot :name="slot" v-bind="slotProps || {}" />
      </template>
    </q-input>
  `,
  inheritAttrs: false,
  props: { modelValue: null },
  emits: ["update:modelValue"],
  data() {
    return { shown: this.modelValue, typing: false, typedIn: null, previewed: null, revision: 0 };
  },
  // Quasar's QInput (v2.18.5, as NiceGUI 3.16 ships it) answers a blur with
  // a `setTimeout` that writes the field's value into its input whatever the
  // input holds (`onFinishEditing`). A field left by Tab and entered again by
  // Shift+Tab before that timer runs — Chrome runs keys ahead of timers — had
  // its first key undone by it: "-" holds no number yet, so the input's value
  // is "", and writing "" over it erased the "-"; `-0.7` was staged as 0.7.
  // So a write of the value its input already holds is no write while the
  // field has the focus: Vue never makes one (it writes a value that
  // differs), and on a number input it can only erase text that is not yet
  // a number ("-", "1e"). Every other write goes through, and a blurred
  // field is still put back to its value. Check this on a Quasar upgrade.
  mounted() {
    const input = this.$refs.qRef.nativeEl;
    const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    Object.defineProperty(input, "value", {
      configurable: true,
      get() {
        return native.get.call(this);
      },
      set(value) {
        const held = String(value ?? "") === native.get.call(this);
        if (held && document.activeElement === this) return;
        native.set.call(this, value);
      },
    });
  },
  computed: {
    display() {
      const previewing = this.previewed !== null && this.previewed !== undefined;
      return previewing && this.typedIn !== this.revision ? this.previewed : this.shown;
    },
  },
  watch: {
    modelValue(value) {
      if (!this.typing) this.shown = value;
    },
  },
  methods: {
    preview(value, revision) {
      this.previewed = value;
      this.revision = revision;
    },
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
