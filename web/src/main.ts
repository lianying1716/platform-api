import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './styles/tokens.css'

// The workspace registers only the Element Plus components it renders.  A
// barrel import of 'element-plus' pulls every component into one ~945 kB
// chunk, which delayed the first screen even though most of it was unused.
import { ElAlert } from 'element-plus/es/components/alert/index.mjs'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElCheckbox, ElCheckboxGroup } from 'element-plus/es/components/checkbox/index.mjs'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElDivider } from 'element-plus/es/components/divider/index.mjs'
import { ElDrawer } from 'element-plus/es/components/drawer/index.mjs'
import { ElEmpty } from 'element-plus/es/components/empty/index.mjs'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElInputNumber } from 'element-plus/es/components/input-number/index.mjs'
import { ElLoading } from 'element-plus/es/components/loading/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElNotification } from 'element-plus/es/components/notification/index.mjs'
import { ElProgress } from 'element-plus/es/components/progress/index.mjs'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index.mjs'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index.mjs'
import { ElStep, ElSteps } from 'element-plus/es/components/steps/index.mjs'
import { ElSwitch } from 'element-plus/es/components/switch/index.mjs'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { ElTabPane, ElTabs } from 'element-plus/es/components/tabs/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import { ElTimeSelect } from 'element-plus/es/components/time-select/index.mjs'
import { provideGlobalConfig } from 'element-plus/es/components/config-provider/index.mjs'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { ElTooltip } from 'element-plus/es/components/tooltip/index.mjs'

// Styles are per component as well; the old full index.css was 450 kB.
import 'element-plus/es/components/base/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/checkbox/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/icon/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/loading/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/notification/style/css'
import 'element-plus/es/components/progress/style/css'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/steps/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/time-select/style/css'
import 'element-plus/es/components/tooltip/style/css'
import 'element-plus/es/components/popper/style/css'
import 'element-plus/es/components/scrollbar/style/css'
import 'element-plus/es/components/overlay/style/css'

// Chosen visual direction: tag the root so all [data-dir='bauhaus'] rules apply.
document.documentElement.dataset.dir = 'bauhaus'

const app = createApp(App)
const components = [
  ElAlert, ElButton, ElCheckbox, ElCheckboxGroup, ElDescriptions, ElDescriptionsItem,
  ElDialog, ElDivider, ElDrawer, ElEmpty, ElForm, ElFormItem, ElIcon, ElInput,
  ElInputNumber, ElOption, ElProgress, ElRadioButton, ElRadioGroup, ElSelect,
  ElStep, ElSteps, ElSwitch, ElTable, ElTableColumn, ElTabPane, ElTabs, ElTag,
  ElTimeSelect, ElTooltip,
]
for (const component of components) {
  // Element Plus exposes a runtime name on every component; the generated
  // type for the mixed array does not preserve that optional property.
  app.component((component as { name?: string }).name || '', component)
}
app.use(ElLoading)
// Component-level locale + global defaults, without importing all of Element.
provideGlobalConfig({ locale: zhCn }, app, true)
// Services used from `api.ts` are module imports; these globals keep the
// conventional `$message` access available for future pages.
app.config.globalProperties.$message = ElMessage
app.config.globalProperties.$msgbox = ElMessageBox
app.config.globalProperties.$notify = ElNotification
app.use(router)
app.mount('#app')
