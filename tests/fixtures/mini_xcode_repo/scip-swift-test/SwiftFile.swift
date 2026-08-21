import Foundation

class Animal {
  let name: String

  init(name: String) {
    self.name = name
  }

  func speak() -> String {
    return "\(name) makes a sound"
  }
}

class Dog: Animal {
  let breed: String

  init(name: String, breed: String) {
    self.breed = breed
    super.init(name: name)
  }

  override func speak() -> String {
    return "\(name) the \(breed) barks"
  }
}

@main
struct EntryPoint {
  static func main() {
    let dog = Dog(name: "Rex", breed: "Labrador")
    print(dog.speak())
  }
}
