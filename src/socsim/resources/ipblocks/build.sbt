scalaVersion := "2.13.12"
libraryDependencies += "org.chipsalliance" %% "chisel" % "6.5.0"
addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % "6.5.0" cross CrossVersion.full)
lazy val inventory = taskKey[Unit]("Write the actual resolved compilation artifacts")
inventory := {
  val files = ((update).value.allFiles ++ scalaInstance.value.allJars).distinct
  IO.write(file("dependencies.paths"), files.map(_.getAbsolutePath).sorted.mkString("\n") + "\n")
}
