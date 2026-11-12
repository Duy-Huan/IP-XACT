// SPDX-License-Identifier: Apache-2.0
// The two algorithm/IO classes are selected verbatim from pinned Chipyard source.
object Generate extends App {
  circt.stage.ChiselStage.emitSystemVerilogFile(
    new chipyard.example.GCDMMIOChiselModule(32),
    args = Array("--target-dir", "rtl"),
    firtoolOpts = Array("-disable-all-randomization",
      "--lowering-options=disallowLocalVariables"))
}
