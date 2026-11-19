// SPDX-License-Identifier: Apache-2.0
// Explicit single RocketTile wrapper; all stateful CPU/cache/PTW behaviour is upstream RTL.
import chisel3._
import org.chipsalliance.cde.config._
import org.chipsalliance.diplomacy.lazymodule._
import org.chipsalliance.diplomacy.bundlebridge._
import freechips.rocketchip.tile._
import freechips.rocketchip.rocket._
import freechips.rocketchip.subsystem._
import freechips.rocketchip.diplomacy.{AddressSet, RegionType, TransferSizes}
import freechips.rocketchip.resources.{DTSTimebase, BindingScope}
import freechips.rocketchip.tilelink._
import freechips.rocketchip.amba.axi4._
import freechips.rocketchip.interrupts._
import freechips.rocketchip.devices.debug.DebugModuleKey

class UspRocket(ramBase: BigInt, ramSize: BigInt, devices: Seq[AXI4SlaveParameters])(implicit p: Parameters) extends LazyModule with BindingScope {
 val tp = RocketTileParams(core=RocketCoreParams(fpu=None,useDebug=false,nBreakpoints=0),
   icache=Some(ICacheParams(nSets=64,nWays=2,rowBits=64)),
   dcache=Some(DCacheParams(nSets=64,nWays=2,rowBits=64,nMSHRs=0)))
 val tile = LazyModule(new RocketTile(tp,RocketCrossingParams(),HartsWontDeduplicate(tp)))
 val ram = AddressSet(ramBase,ramSize-1)
 val axi = AXI4SlaveNode(Seq(AXI4SlavePortParameters(slaves=Seq(
   AXI4SlaveParameters(address=Seq(ram),regionType=RegionType.UNCACHED,executable=true,
     supportsRead=TransferSizes(1,64),supportsWrite=TransferSizes(1,64),interleavedId=Some(0))
 )++devices,beatBytes=8)))
 axi := AXI4Buffer() := AXI4UserYanker() := AXI4IdIndexer(4) := TLToAXI4() := TLWidthWidget(8) := TLBroadcast(64) := tile.masterNode
 val irq = IntSourceNode(IntSourcePortSimple(num=5))
 tile.intInwardNode := irq
 val hart = BundleBridgeSource(() => UInt(1.W)); tile.hartIdNode := hart
 val boot = BundleBridgeSource(() => UInt(32.W)); tile.resetVectorNode := boot
 val trace = BundleBridgeSink[TraceBundle](); trace := tile.traceNode
 val halt = IntSinkNode(IntSinkPortSimple());halt := tile.haltNode
 val wfi = IntSinkNode(IntSinkPortSimple());wfi := tile.wfiNode
 val cease = IntSinkNode(IntSinkPortSimple());cease := tile.ceaseNode
 override lazy val module = new LazyModuleImp(this) {
   val mem = axi.makeIOs()
   val reset_vector = IO(Input(UInt(32.W)))
   val interrupts = IO(Input(UInt(5.W)))
   val retired = IO(Output(Bool())); val trapped=IO(Output(Bool()))
   val pc = IO(Output(UInt(64.W))); val instruction = IO(Output(UInt(32.W)))
   hart.bundle := 0.U;boot.bundle := reset_vector
   irq.out.head._1 := interrupts.asBools
   val t=trace.bundle.insns.head
   retired := t.valid && !t.exception;trapped := t.valid && t.exception
   pc := t.iaddr;instruction := t.insn
 }
}
object Generate extends App {
 require(args.length==3,"explicit RAM base, size and validated address-map file required")
 val ramBase=BigInt(args(0));val ramSize=BigInt(args(1))
 require(ramBase>=0 && ramSize>=4096 && (ramSize & (ramSize-1))==0)
 require((ramBase & (ramSize-1))==0 && ramBase+ramSize<=(BigInt(1)<<32))

 val raw = scala.io.Source.fromFile(args(2));val input=try raw.mkString finally raw.close()
 val parsed=org.json4s.jackson.JsonMethods.parse(input)
 val devices=parsed.children.map { record =>
   val fields=record.asInstanceOf[org.json4s.JObject].obj.toMap
   require(fields.keySet==Set("base","size","kind"))
   val base=fields("base").asInstanceOf[org.json4s.JInt].num
   val size=fields("size").asInstanceOf[org.json4s.JInt].num
   val kind=fields("kind").asInstanceOf[org.json4s.JString].s
   require(base>=0 && size>0 && base+size<=(BigInt(1)<<32) && Set("rom","mmio")(kind))
   AXI4SlaveParameters(address=AddressSet.misaligned(base,size),regionType=RegionType.GET_EFFECTS,
     executable=kind=="rom",supportsRead=TransferSizes(1,8),
     supportsWrite=if(kind=="rom")TransferSizes.none else TransferSizes(1,8),interleavedId=Some(0))
 }
 implicit val p: Parameters = new Config((site,here,up) => {
   case MaxHartIdBits => 1
   case DebugModuleKey => None
   case SystemBusKey => SystemBusParams(beatBytes=8,blockBytes=64)
   case DTSTimebase => BigInt(10000000)
 })
 circt.stage.ChiselStage.emitCHIRRTLFile(LazyModule(new UspRocket(ramBase,ramSize,devices)).module,
   Array("--target-dir","rtl"))
}
